# -*- coding: utf-8 -*-
"""Робот: нові пости Telegram-каналу → картки товарів у магазині.

Запускається GitHub Actions кожні 10 хвилин (.github/workflows/tg-sync.yml).
Бере нові пости каналу через бота (бот має бути адміністратором каналу), групує фото в товари
так само, як імпорт архіву на сайті (ключ товару 'tg:<номер першого повідомлення>' — тому архів
і робот не створюють дублів), питає в Gemini коротку назву й розділ, ціну бере з тексту поста
і створює картку в базі магазину. Пост за шаблоном власника (рядок «Ціна: …», див. parse_template) —
назва й розділ беруться з самого поста, без Gemini.

Секрети (GitHub → Settings → Secrets and variables → Actions):
  TG_BOT_TOKEN              — токен бота від @BotFather
  GEMINI_API_KEY            — ключ Gemini (aistudio.google.com/apikey)
  FIREBASE_SERVICE_ACCOUNT  — увесь вміст JSON-файлу ключа сервісного акаунта Firebase
Необов'язкові змінні (там само, вкладка Variables):
  TG_CHANNEL    — канал без @ (типово premium_luxe_select)
  TG_PUBLISH    — 1: одразу в магазин (типово), 0: у чернетки
  GEMINI_MODEL  — модель (типово gemini-3.5-flash-lite)
Поки секретів немає, робот нічого не робить і завершується без помилки.
"""
import base64
import json
import os
import random
import re
import sys
import time
import uuid
from urllib.parse import quote

import requests

CHANNEL = (os.environ.get('TG_CHANNEL') or 'premium_luxe_select').lstrip('@').lower()
PUBLISH = (os.environ.get('TG_PUBLISH') or '1').strip() != '0'
MODEL = os.environ.get('GEMINI_MODEL') or 'gemini-3.5-flash-lite'
BUCKET = os.environ.get('FIREBASE_BUCKET') or 'premium-luxe-shop.firebasestorage.app'

MAX_PHOTOS = 10                 # як на сайті
AI_PHOTOS = 2                   # скільки фото показуємо Gemini
RIPE_SEC = 180                  # чекаємо 3 хв після останнього фото: ціну часто пишуть окремим повідомленням
TEXT_WINDOW = 120               # текст без фото приклеюється до товару, якщо написаний не пізніше ніж за 2 хв
STORAGE_LIMIT = 1024 ** 3       # 1 ГБ, як на сайті
SKU_DIGITS = 5
DEFAULT_CATEGORIES = ['Жіночий одяг', 'Чоловічий одяг', 'Верхній одяг', 'Взуття', 'Аксесуари']


class Retry(Exception):
    """Тимчасова проблема: зупиняємось, пост лишається в черзі до наступного запуску."""


# ---------- Розбір постів (без мережі — це перевіряється тестом) ----------

NUM = r'(?<![\w@])(\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?!\d)|\d+)'   # «4900», «4 900»; не частина слова («@yuli666666»)
NOTE = r'\s*(\+[^\n]{0,40})?'                                    # «+ доставка», «+вага»


def _price(m):
    price = int(re.sub(r'[ \u00a0\u202f]', '', m.group(1)))
    return price, ('+ ' + m.group(2)[1:].strip()) if m.group(2) else ''


def parse_price(text):
    """Ціна з тексту поста (так само, як на сайті):
    1) після слова «ціна» («Ціна: 4900 грн», «ціна 6100»); 2) число з «грн»;
    3) число з «+доставка», «+вага», «з вагою»;
    4) інакше останнє число від 100, крім розмірів «40-45», «50%», «1250$», «1:1», «25 см».
    «2250+вага» → 2250 і примітка «+ вага»."""
    t = str(text or '')
    m = (re.search(r'ціна\s*:?\s*' + NUM + r'\s*(?:грн\.?|uah|₴)?' + NOTE, t, re.I)
         or re.search(NUM + r'\s*(?:грн|uah|₴)' + NOTE, t, re.I)
         or re.search(NUM + r'(?=\s*(?:[+*]\s*(?:доставк|ваг)|з\s+(?:вагою|доставкою)))' + NOTE, t, re.I))
    if m:
        return _price(m)
    best = None
    for m in re.finditer(NUM + NOTE, t):
        s, e = m.start(1), m.end(1)
        if (re.search(r'\d\s?[-–/:.,]\s?$', t[max(0, s - 3):s])          # кінець діапазону, «1:1», дата
                or re.match(r'\s?[-–/:.,]\s?\d', t[e:e + 3])             # початок діапазону «40-45»
                or re.match(r'\s*(%|\$|€|usd|eur|см|мм|cm|mm|кг|мл|шт|г\b)', t[e:e + 8], re.I)):
            continue
        if 100 <= int(re.sub(r'[ \u00a0\u202f]', '', m.group(1))) <= 999999:
            best = m
    return _price(best) if best else (None, '')


def parse_template(text):
    """Пост за шаблоном власника — у ньому є рядок «Ціна: …»:
        Кросівки Loro Piana      ← 1-й рядок — назва
        Ціна: 4900 грн
        Розмір: 40-45            ← необов'язково, робот не чіпає
        Розділ: Чоловікам        ← необов'язково
    Повертає {'title', 'category'} або None, якщо пост не за шаблоном."""
    lines = [l.strip() for l in str(text or '').splitlines() if l.strip()]
    if not any(re.match(r'ціна\s*:', l, re.I) for l in lines):
        return None
    first = '' if re.match(r'(ціна|розміри?|розділ)\s*:', lines[0], re.I) else lines[0]
    cat = next((re.sub(r'^розділ\s*:\s*', '', l, flags=re.I).strip() for l in lines if re.match(r'розділ\s*:', l, re.I)), '')
    return {'title': re.sub(r'[\s,.;:–—-]+$', '', first)[:90], 'category': cat}


def short_title(title):
    """Назва з 2–3 слів."""
    words = str(title or '').split()[:3]
    return re.sub(r'[\s,.;:–—-]+$', '', ' '.join(words))[:90]


def post_media(msg):
    """Найбільше фото поста (або картинка, надіслана файлом). Відео робот не бере."""
    if msg.get('photo'):
        best = max(msg['photo'], key=lambda p: p.get('file_size') or p.get('width', 0) * p.get('height', 0))
        return {'file_id': best['file_id']}
    doc = msg.get('document')
    if doc and str(doc.get('mime_type', '')).startswith('image/') and (doc.get('file_size') or 0) <= 20 * 1024 * 1024:
        return {'file_id': doc['file_id']}
    return None


def build_groups(updates, channel):
    """Групує пости каналу в товари. Правило те саме, що в імпорті архіву на сайті:
    фото з різницею в часі до 1 с — один товар (до 10 фото), текст без фото протягом 2 хв — його підпис."""
    groups, cur = [], None
    for u in updates:
        msg = u.get('channel_post')
        if not msg or str((msg.get('chat') or {}).get('username', '')).lower() != channel:
            continue
        t = int(msg.get('date') or 0)
        text = (msg.get('caption') or msg.get('text') or '').strip()
        media = post_media(msg)
        if not media:
            if text and cur and not cur['text'] and t - cur['t'] <= TEXT_WINDOW:
                cur['text'] = text
                cur['last_update'] = u['update_id']
            continue
        if not cur or abs(t - cur['t']) > 1 or len(cur['files']) >= MAX_PHOTOS:
            cur = {'t': t, 'last_t': t, 'files': [], 'text': '', 'key': 'tg:%s' % msg['message_id'],
                   'first_update': u['update_id'], 'last_update': u['update_id']}
            groups.append(cur)
        cur['files'].append(media)
        cur['last_t'] = max(cur['last_t'], t)
        cur['last_update'] = u['update_id']
        if text:
            cur['text'] = (cur['text'] + '\n' + text) if cur['text'] else text
    return groups


def plan(updates, channel, now, batch_full=False):
    """Що обробляти зараз і до якого update_id можна підтвердити прочитане.
    Товар «дозрів», якщо після останнього фото минуло RIPE_SEC. Якщо пачка оновлень повна (100),
    останній товар відкладаємо: його фото можуть бути в наступній пачці."""
    groups = build_groups(updates, channel)
    ready = []
    for i, g in enumerate(groups):
        if now - g['last_t'] < RIPE_SEC or (batch_full and len(groups) > 1 and i == len(groups) - 1):
            # підтверджуємо все, що було до цього товару
            before = [u['update_id'] for u in updates if u['update_id'] < g['first_update']]
            return ready, (max(before) + 1 if before else None)
        ready.append(g)
    return ready, (updates[-1]['update_id'] + 1 if updates else None)


LETTERS_RE = re.compile(r'[A-ZА-ЯІЇЄҐЁ]')


def make_cat_code(name, taken):
    """Двобуквений код розділу для артикула (Жіночий одяг → ЖО) — як на сайті."""
    words = [''.join(LETTERS_RE.findall(w.upper())) for w in str(name).split()]
    words = [w for w in words if w]
    letters = LETTERS_RE.findall(str(name).upper())
    cands = []
    if len(words) >= 2:
        cands.append(words[0][0] + words[1][0])
    if len(letters) >= 2:
        cands.append(letters[0] + letters[1])
    if len(words) >= 2:
        cands += [words[0][0] + c for c in words[1][1:]]
    cands += [letters[0] + c for c in letters[2:]] if letters else []
    for c in cands:
        if len(c) == 2 and c not in taken:
            return c
    base = letters[0] if letters else 'Т'
    for n in range(1, 100):
        if base + str(n) not in taken:
            return base + str(n)
    return 'ТВ'


def build_prompt(settings, text):
    cats = settings.get('categories') or DEFAULT_CATEGORIES
    extra = (settings.get('geminiInstructions') or '').strip()
    return (
        'Ти — контент-менеджер інтернет-магазину одягу «%s». Проаналізуй фото товару%s.\n'
        'Поверни ТІЛЬКИ JSON українською мовою:\n'
        '{"title":"назва з 2-3 слів, не більше: вид товару і бренд або колір","category":"одна категорія","newCategory":false}\n'
        'Правила:\n'
        '- Якщо на фото комплект (наприклад, куртка + спідниця) — назви його комплектом.\n'
        '- Не вигадуй бренд, якщо його не видно чітко.\n'
        '- Існуючі категорії: %s. Обирай з них; нову пропонуй (newCategory=true) лише якщо жодна не підходить.%s'
    ) % (
        settings.get('storeName') or 'Premium select',
        (' і текст поста з Telegram: """%s"""' % text) if text else '',
        json.dumps(cats, ensure_ascii=False),
        ('\n\nПобажання власника магазину до назви (виконуй, але назва все одно 2-3 слова, відповідь лише JSON):\n"""%s"""' % extra) if extra else '',
    )


# ---------- Telegram ----------

def tg(method, **params):
    token = os.environ['TG_BOT_TOKEN']
    try:
        r = requests.post('https://api.telegram.org/bot%s/%s' % (token, method), json=params, timeout=60)
        data = r.json()
    except Exception as e:  # у тексті помилки requests є адреса з токеном — не друкуємо її
        raise Retry('Telegram не відповідає (%s)' % type(e).__name__)
    if not data.get('ok'):
        raise Retry('Telegram %s: %s' % (method, data.get('description')))
    return data['result']


def tg_download(file_id):
    token = os.environ['TG_BOT_TOKEN']
    info = tg('getFile', file_id=file_id)
    try:
        r = requests.get('https://api.telegram.org/file/bot%s/%s' % (token, info['file_path']), timeout=120)
        r.raise_for_status()
    except Exception as e:
        raise Retry('Не вдалося завантажити фото з Telegram (%s)' % type(e).__name__)
    return r.content


# ---------- Gemini ----------

def gemini(parts, tries=4):
    url = 'https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent' % MODEL
    body = {'contents': [{'role': 'user', 'parts': parts}],
            'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0.4}}
    delays = [5, 15, 45]
    last = ''
    for a in range(tries):
        try:
            r = requests.post(url, headers={'x-goog-api-key': os.environ['GEMINI_API_KEY']}, json=body, timeout=90)
        except Exception as e:
            last = 'Gemini не відповідає (%s)' % type(e).__name__
        else:
            if r.ok:
                try:
                    cand = (r.json().get('candidates') or [{}])[0]
                    txt = ''.join(p.get('text', '') for p in (cand.get('content') or {}).get('parts', []))
                    j = json.loads(re.sub(r'```json|```', '', txt).strip())
                    return j[0] if isinstance(j, list) and j else j
                except Exception:
                    last = 'Gemini повернув незрозумілу відповідь'
            else:
                try:
                    why = r.json().get('error', {}).get('message', '')
                except Exception:
                    why = ''
                last = 'Gemini помилка %s: %s' % (r.status_code, why[:200])
                if r.status_code not in (429,) and r.status_code < 500:
                    raise Retry(last)  # ключ/модель — повтори не допоможуть
        if a < tries - 1:
            time.sleep(delays[min(a, len(delays) - 1)])
    raise Retry(last)


# ---------- Магазин (Firebase) ----------

def connect():
    import firebase_admin
    from firebase_admin import credentials, firestore, storage
    cred = credentials.Certificate(json.loads(os.environ['FIREBASE_SERVICE_ACCOUNT']))
    firebase_admin.initialize_app(cred, {'storageBucket': BUCKET})
    return firestore.client(), storage.bucket()


def new_id():
    def b36(n):
        s = ''
        while n:
            n, r = divmod(n, 36)
            s = '0123456789abcdefghijklmnopqrstuvwxyz'[r] + s
        return s or '0'
    return 'p' + b36(int(time.time() * 1000)) + ''.join(random.choice('0123456789abcdefghijklmnopqrstuvwxyz') for _ in range(5))


def resolve_category(db, settings, cat):
    """Розділ із наявних; новий додаємо, лише якщо це дозволено в налаштуваннях магазину."""
    from firebase_admin import firestore
    cats = settings.get('categories') or DEFAULT_CATEGORIES
    cat = str(cat or '').strip()
    for c in cats:
        if c.lower() == cat.lower():
            return c
    if cat and settings.get('allowNewCategories') is not False:
        db.document('catalog/fashionStyleData').set({'categories': firestore.ArrayUnion([cat])}, merge=True)
        settings['categories'] = cats + [cat]
        return cat
    return cats[0] if cats else cat


def auto_sku(db, settings, cat):
    from firebase_admin import firestore
    name = (cat or '').strip() or 'Товар'
    codes = settings.get('catCodes') if isinstance(settings.get('catCodes'), dict) else {}
    code = codes.get(name)
    if not code:
        code = make_cat_code(name, set(codes.values()))
        codes[name] = code
        settings['catCodes'] = codes
        db.document('catalog/fashionStyleData').set({'catCodes': {name: code}}, merge=True)
    ref = db.document('catalog/skuCounters')

    @firestore.transactional
    def reserve(tx):
        snap = ref.get(transaction=tx)
        cur = int((snap.to_dict() or {}).get(code) or 0) if snap.exists else 0
        tx.set(ref, {code: cur + 1}, merge=True)
        return cur + 1

    return '%s-%s' % (code, str(reserve(db.transaction())).zfill(SKU_DIGITS))


def create_product(db, bucket, settings, g, state):
    from firebase_admin import firestore
    from google.cloud.firestore_v1.base_query import FieldFilter
    if list(db.collection('products').where(filter=FieldFilter('sourceKey', '==', g['key'])).limit(1).stream()):
        print('⏭ %s — уже є в магазині' % g['key'])
        return
    photos = [tg_download(f['file_id']) for f in g['files'][:MAX_PHOTOS]]
    total = sum(len(b) for b in photos)
    used = int(((db.document('catalog/storageStats').get().to_dict()) or {}).get('usedBytes') or 0)
    if used + total > STORAGE_LIMIT:
        raise Retry('У сховищі магазину закінчилося місце (1 ГБ). Видаліть старі товари в адмінці — робот продовжить сам.')

    # пост за шаблоном: назва з 1-го рядка, розділ з «Розділ:» (лише наявний) — Gemini потрібен тільки для решти
    tpl = parse_template(g['text']) or {}
    cats = settings.get('categories') or DEFAULT_CATEGORIES
    tpl_cat = next((c for c in cats if c.lower() == tpl.get('category', '').lower()), '')
    ai = {}
    if not (tpl.get('title') and tpl_cat):
        parts = [{'text': build_prompt(settings, g['text'])}]
        parts += [{'inline_data': {'mime_type': 'image/jpeg', 'data': base64.b64encode(b).decode()}} for b in photos[:AI_PHOTOS]]
        ai = gemini(parts) or {}
    price, note = parse_price(g['text'])
    category = tpl_cat or resolve_category(db, settings, ai.get('category'))
    title = tpl.get('title') or short_title(ai.get('title'))
    now = int(time.time() * 1000)
    pid = new_id()
    # без ціни в магазин не публікуємо — такий товар іде в чернетки, щоб власник дописав ціну
    status = 'published' if PUBLISH and price else 'draft'
    p = {
        'id': pid, 'title': title, 'price': price or 0, 'priceNote': note,
        'category': category, 'filters': {}, 'color': '', 'material': '', 'images': [], 'video': None, 'desc': '',
        'status': status, 'createdAt': now, 'importId': state['import_id'], 'sourceKey': g['key'],
        'source': g['text'] or '', 'pinned': False, 'sku': auto_sku(db, settings, category),
    }
    blobs = []
    try:
        for b in photos:
            path = 'products/%d_%s.jpg' % (int(time.time() * 1000), ''.join(random.choice('0123456789abcdefghijklmnopqrstuvwxyz') for _ in range(6)))
            token = str(uuid.uuid4())
            blob = bucket.blob(path)
            blob.metadata = {'firebaseStorageDownloadTokens': token}
            blob.upload_from_string(b, content_type='image/jpeg')
            blobs.append(blob)
            p['images'].append({
                'url': 'https://firebasestorage.googleapis.com/v0/b/%s/o/%s?alt=media&token=%s' % (BUCKET, quote(path, safe=''), token),
                'path': path, 'size': len(b)})
        db.collection('products').document(pid).set(p)
    except Exception as e:
        for blob in blobs:
            try:
                blob.delete()
            except Exception:
                pass
        raise Retry('Не вдалося зберегти товар (%s: %s)' % (type(e).__name__, str(e)[:150]))
    db.document('catalog/storageStats').set(
        {'usedBytes': firestore.Increment(total), 'fileCount': firestore.Increment(len(photos))}, merge=True)
    imp = db.collection('imports').document(state['import_id'])
    if not state['import_saved']:
        imp.set({'mode': 'Telegram-бот', 'createdAt': now, 'count': 0, 'productIds': [], 'note': '@' + CHANNEL})
        state['import_saved'] = True
    imp.update({'productIds': firestore.ArrayUnion([pid]), 'count': firestore.Increment(1)})
    state['added'] += 1
    print('✅ %s → «%s», %s грн, %s, фото: %d, %s' % (
        g['key'], p['title'], p['price'], category, len(photos), 'в магазині' if status == 'published' else 'чернетка'))


# ---------- Запуск ----------

def check():
    """Перевірка налаштувань: нічого не створює в магазині."""
    ok = True

    def step(name, fn):
        nonlocal ok
        try:
            print('✅ %s: %s' % (name, fn()))
        except Exception as e:
            ok = False
            print('❌ %s: %s' % (name, str(e)[:300]))

    def bot():
        me = tg('getMe')
        m = tg('getChatMember', chat_id='@' + CHANNEL, user_id=me['id'])
        if m.get('status') not in ('administrator', 'creator'):
            raise Exception('бот @%s не адміністратор каналу @%s' % (me.get('username'), CHANNEL))
        return 'бот @%s — адміністратор каналу @%s' % (me.get('username'), CHANNEL)

    def shop():
        db, bucket = connect()
        s = db.document('catalog/fashionStyleData').get().to_dict() or {}
        n = db.collection('products').count().get()[0][0].value
        bucket.blob('products/__check__').exists()  # перевірка доступу до сховища
        return 'магазин «%s», товарів: %d, розділи: %s' % (s.get('storeName'), n, ', '.join(s.get('categories') or []))

    step('Telegram', bot)
    step('Gemini', lambda: 'відповідає, модель %s (%s)' % (MODEL, gemini([{'text': 'Поверни JSON {"ok":true}'}], tries=2)))
    step('Firebase', shop)
    return 0 if ok else 1


def main():
    missing = [k for k in ('TG_BOT_TOKEN', 'GEMINI_API_KEY', 'FIREBASE_SERVICE_ACCOUNT') if not os.environ.get(k)]
    if missing:
        print('Робот не налаштований — немає секретів: %s. Нічого не роблю.' % ', '.join(missing))
        return 0
    if '--check' in sys.argv:
        return check()
    db, bucket = connect()
    settings = db.document('catalog/fashionStyleData').get().to_dict() or {}
    state = {'import_id': 'imp%d' % int(time.time() * 1000), 'import_saved': False, 'added': 0}
    try:
        for _ in range(20):  # до 20 пачок по 100 оновлень за один запуск
            updates = tg('getUpdates', timeout=0, limit=100, allowed_updates=['channel_post'])
            if not updates:
                break
            ready, confirm = plan(updates, CHANNEL, int(time.time()), batch_full=len(updates) >= 100)
            done_to = None
            try:
                for g in ready:
                    create_product(db, bucket, settings, g, state)
                    done_to = g['last_update'] + 1
            finally:
                # підтверджуємо лише те, що справді оброблено (або все «дозріле», якщо помилок не було)
                upto = confirm if (not ready or done_to == ready[-1]['last_update'] + 1) else done_to
                if upto:
                    tg('getUpdates', offset=upto, timeout=0, limit=1, allowed_updates=['channel_post'])
            full = len(updates) >= 100
            if not upto or (confirm <= updates[-1]['update_id'] and not full):
                break  # далі йдуть ще «недозрілі» пости — дочекаємось наступного запуску
    except Retry as e:
        print('⏸ Зупинка до наступного запуску: %s' % e)
        print('Додано товарів: %d' % state['added'])
        return 1
    print('Додано товарів: %d' % state['added'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
