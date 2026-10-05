#!/usr/bin/env python3
"""
tools/dd_auto.py — Автоматичне оформлення замовлень на DD Tuning через Playwright
Запуск з API: POST /api/dd-order  { orderId }
Прямий запуск: python tools/dd_auto.py <orderId>

STEPS:
  1. Відкриваємо Chrome з людськими fingerprintами
  2. Заходимо в дилерський кабінет DD Tuning
  3. Додаємо кожен товар до корзини (пошук за артикулом)
  4. Оформлюємо доставку (Нова Пошта, дані покупця)
  5. Зберігаємо номер замовлення DD
"""

import os, sys, json, time, random, math, asyncio
from pathlib import Path
from datetime import datetime

try:
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout
except ImportError:
    sys.exit("pip install playwright && playwright install chromium")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / '.env')
except ImportError:
    pass

# ── Налаштування ────────────────────────────────────────────────────────────────
DD_BASE     = 'https://ddtuning.com.ua'
DD_LOGIN    = os.environ.get('DD_LOGIN', '')
DD_PASSWORD = os.environ.get('DD_PASSWORD', '')
DATA_DIR    = Path(__file__).parent.parent / 'data'

# ── Людська емуляція миші ─────────────────────────────────────────────────

def bezier(t, p0, p1, p2, p3):
    """Cubic Bezier для плавного руху миші."""
    u = 1 - t
    return (u**3 * p0 + 3*u**2*t * p1 + 3*u*t**2 * p2 + t**3 * p3)

async def human_move(page, x: float, y: float):
    """Рух миші по Bezier-кривій з випадковими тривожннями."""
    cur = await page.evaluate("() => ({ x: window._mx || 400, y: window._my || 300 })")
    cx, cy = cur['x'], cur['y']

    # Випадкові контрольні точки Bezier
    cp1x = cx + random.uniform(-100, 100)
    cp1y = cy + random.uniform(-80,  80)
    cp2x = x  + random.uniform(-80,  80)
    cp2y = y  + random.uniform(-60,  60)

    steps = random.randint(18, 32)
    for i in range(steps + 1):
        t  = i / steps
        # Нелінійний easing (людина прискорюється початку і сповільнюється в кінці)
        ease = t * t * (3 - 2*t)
        mx   = bezier(ease, cx, cp1x, cp2x, x)
        my   = bezier(ease, cy, cp1y, cp2y, y)
        # Мікро-тривожність
        mx  += random.gauss(0, 0.4)
        my  += random.gauss(0, 0.4)
        await page.mouse.move(mx, my)
        await asyncio.sleep(random.uniform(0.008, 0.025))

    # Зберігаємо поточну позицію
    await page.evaluate(f"() => {{ window._mx = {x}; window._my = {y}; }}")

async def human_click(page, selector: str = None, x: float = None, y: float = None):
    """Клік: спочатку переміщуємо мишу, потім клікаємо."""
    if selector:
        el  = page.locator(selector).first
        box = await el.bounding_box()
        if not box:
            await el.click()
            return
        tx = box['x'] + box['width']  * random.uniform(0.3, 0.7)
        ty = box['y'] + box['height'] * random.uniform(0.3, 0.7)
    else:
        tx, ty = x, y

    await human_move(page, tx, ty)
    await asyncio.sleep(random.uniform(0.05, 0.18))  # пауза перед кліком
    await page.mouse.click(tx, ty)

async def human_type(page, selector: str, text: str):
    """Друкує зі швидкістю людини (30–90 зн.вхв), іноді робить паузу."""
    await human_click(page, selector)
    await asyncio.sleep(random.uniform(0.2, 0.5))

    for char in text:
        await page.keyboard.type(char)
        # 30–90 знаків/хв = 0.033–0.1 сек/знак
        delay = random.gauss(0.07, 0.025)
        delay = max(0.03, min(delay, 0.22))
        # Іноді пауза між словами
        if char == ' ' and random.random() < 0.15:
            delay += random.uniform(0.1, 0.4)
        await asyncio.sleep(delay)

async def human_scroll(page, direction='down', amount=None):
    """Плавний скрол з випадковою швидкістю."""
    if amount is None:
        amount = random.randint(200, 600)
    dy = amount if direction == 'down' else -amount

    steps = random.randint(4, 8)
    for i in range(steps):
        chunk = dy / steps + random.gauss(0, dy / steps * 0.2)
        await page.mouse.wheel(0, chunk)
        await asyncio.sleep(random.uniform(0.05, 0.15))

async def pause(min_s=0.5, max_s=2.0):
    """Людська пауза між діями."""
    await asyncio.sleep(random.uniform(min_s, max_s))


# ── Основна логіка ────────────────────────────────────────────────────────────────

async def login(page):
    print('🔑 Вход в дилерський кабінет DD Tuning...')
    await page.goto(f'{DD_BASE}/auth/login', wait_until='domcontentloaded')
    await pause(1.5, 3.0)

    # Вводимо email
    await human_type(page, 'input[name="email"], input[type="email"]', DD_LOGIN)
    await pause(0.4, 1.0)

    # Вводимо пароль
    await human_type(page, 'input[name="password"], input[type="password"]', DD_PASSWORD)
    await pause(0.5, 1.2)

    # Трохи скролимо (людина часто скролить перед кліком)
    if random.random() < 0.4:
        await human_scroll(page, 'down', random.randint(50, 150))
        await pause(0.3, 0.8)

    # Клік «Увійти»
    await human_click(page, 'button[type="submit"], .login-btn, input[type="submit"]')
    await page.wait_for_load_state('networkidle', timeout=15000)
    await pause(1.0, 2.5)

    if '/auth/login' in page.url:
        raise RuntimeError('Невірний логін або пароль. Перевірте DD_LOGIN / DD_PASSWORD.')
    print('✅ Увійшли в дилерський кабінет')


async def add_to_cart(page, sku: str, qty: int):
    """Шукає товар за артикулом і додає до корзини."""
    print(f'   🔍 {sku} × {qty}...')

    # Пошук
    search_url = f'{DD_BASE}/search.html?search={sku}'
    await page.goto(search_url, wait_until='domcontentloaded')
    await pause(1.5, 3.0)

    # Скролимо, щоб виглядати живо
    await human_scroll(page, 'down', random.randint(100, 300))
    await pause(0.5, 1.5)

    # Перевіряємо чи є точний результат
    cards = page.locator('.product-item, .catalog-item, [data-sku]')
    count = await cards.count()

    if count == 0:
        print(f'   ⚠️  {sku}: товар не знайдено')
        return False

    # Клік на перший результат
    await human_click(page, '.product-item:first-child a, .catalog-item:first-child a')
    await page.wait_for_load_state('domcontentloaded')
    await pause(1.0, 2.5)

    # Передоманий пред-початок: читаємо сторінку
    await human_scroll(page, 'down', random.randint(150, 400))
    await pause(0.8, 2.0)

    # Кількість
    qty_sel = 'input.es-cnt, input[name="quantity"], .qty-input'
    qty_el  = page.locator(qty_sel).first
    if await qty_el.count() > 0 and qty > 1:
        await human_click(page, qty_sel)
        await page.keyboard.select_all()
        await human_type(page, qty_sel, str(qty))
        await pause(0.3, 0.8)

    # Кнопка «Купити» (або «Додати в кошик»)
    buy_sel = '.es-buyBtn, button.buy-btn, .add-to-cart, [data-action="buy"]'
    buy_el  = page.locator(buy_sel).first

    if await buy_el.count() == 0:
        # Можливо передзамовлення
        preorder = page.locator('.preorder-button, .pre-order').first
        if await preorder.count() > 0:
            print(f'   ⚠️  {sku}: тільки передзамовлення')
            return False

    await human_click(page, buy_sel)
    await pause(0.8, 1.8)
    print(f'   ✅ {sku} додано до корзини')
    return True


async def fill_checkout(page, order: dict):
    """Заповнює форму оформлення замовлення."""
    print('📝 Форма доставки...')
    await page.goto(f'{DD_BASE}/checkout', wait_until='domcontentloaded')
    await pause(1.5, 3.0)

    customer = order.get('customer', {})
    delivery = order.get('delivery', {})

    # Ім'я
    await human_type(page,
        'input[name="name"], #name, input[placeholder*="\u0406\u043c\u02bc"]',
        customer.get('name', '')
    )
    await pause(0.3, 0.8)

    # Телефон
    await human_type(page,
        'input[name="phone"], #phone, input[type="tel"]',
        customer.get('phone', '')
    )
    await pause(0.3, 0.8)

    # Email (якщо є поле)
    email_el = page.locator('input[name="email"], input[type="email"]').first
    if await email_el.count() > 0 and customer.get('email'):
        await human_type(page, 'input[name="email"]', customer['email'])
        await pause(0.2, 0.6)

    # Місто (Нова Пошта autocomplete)
    if delivery.get('city'):
        city_sel = 'input[name="city"], .city-input, #city'
        await human_type(page, city_sel, delivery['city'])
        await pause(1.2, 2.5)  # чекаємо autocomplete
        # Перша пропозиція
        suggestion = page.locator('.np-suggestion:first-child, .autocomplete-item:first-child').first
        if await suggestion.count() > 0:
            await human_click(page, '.np-suggestion:first-child, .autocomplete-item:first-child')
            await pause(0.5, 1.2)

    # Відділення НП
    if delivery.get('warehouse'):
        wh_sel = 'input[name="warehouse"], .warehouse-input, #warehouse'
        await human_type(page, wh_sel, delivery['warehouse'])
        await pause(1.0, 2.0)
        suggestion = page.locator('.np-suggestion:first-child, .autocomplete-item:first-child').first
        if await suggestion.count() > 0:
            await human_click(page, '.np-suggestion:first-child, .autocomplete-item:first-child')
            await pause(0.5, 1.0)

    # Оплата: накладний платіж
    cod_sel = 'input[value="cod"], label:has-text("\u043d\u0430\u043a\u043b\u0430\u0434\u043d\u0438\u0439"), .payment-cod'
    cod_el  = page.locator(cod_sel).first
    if await cod_el.count() > 0:
        await human_click(page, cod_sel)
        await pause(0.3, 0.8)

    # Коментар (необов'язково)
    comment = order.get('comment', '')
    if comment:
        comment_sel = 'textarea[name="comment"], #comment'
        comment_el  = page.locator(comment_sel).first
        if await comment_el.count() > 0:
            await human_type(page, comment_sel, comment)
            await pause(0.3, 0.8)

    # Переглядаємо форму перед відправкою (людина зазвичай скролить)
    await human_scroll(page, 'up', random.randint(200, 500))
    await pause(0.5, 1.5)
    await human_scroll(page, 'down', random.randint(300, 700))
    await pause(0.8, 2.0)


async def submit_order(page) -> str:
    """Натискає «Оформити замовлення» і повертає номер замовлення DD."""
    submit_sel = 'button[type="submit"].checkout-btn, .order-submit, button:has-text("\u041e\u0444\u043e\u0440\u043c\u0438\u0442\u0438")'
    await human_click(page, submit_sel)
    await page.wait_for_load_state('networkidle', timeout=20000)
    await pause(1.5, 3.0)

    # Шукаємо номер замовлення на сторінці підтвердження
    order_num = ''
    for sel in [
        '.order-number', '#order-number',
        'h1:has-text("\u0437\u0430\u043c\u043e\u0432"), [data-order-id]',
    ]:
        el = page.locator(sel).first
        if await el.count() > 0:
            order_num = (await el.inner_text()).strip()
            break

    return order_num


# ── Головна функція ────────────────────────────────────────────────────────────────

async def process_order(order: dict) -> dict:
    if not DD_LOGIN or not DD_PASSWORD:
        return {'ok': False, 'error': 'DD_LOGIN або DD_PASSWORD не задані в .env'}

    # Випадковий viewport (реальні розміри моніторів)
    viewports = [
        (1366, 768), (1440, 900), (1920, 1080),
        (1280, 800), (1536, 864), (1600, 900),
    ]
    vw, vh = random.choice(viewports)

    # Реальний User-Agent Chrome
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.182 Safari/537.36',
    ]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-infobars',
                f'--window-size={vw},{vh}',
            ]
        )

        ctx = await browser.new_context(
            viewport={'width': vw, 'height': vh},
            user_agent=random.choice(user_agents),
            locale='uk-UA',
            timezone_id='Europe/Kiev',
            # Підробляємо WebGL renderer
            extra_http_headers={'Accept-Language': 'uk-UA,uk;q=0.9,ru;q=0.8'},
        )

        # Приховуємо ознаки Playwright
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['uk-UA','uk','ru'] });
            window.chrome = { runtime: {} };
            window._mx = Math.round(Math.random() * 800 + 200);
            window._my = Math.round(Math.random() * 400 + 200);
        """)

        page = await ctx.new_page()

        # Випадкова початкова затримка (людина не відкриває сайт моментально)
        await pause(1.0, 4.0)

        try:
            await login(page)

            # Очищаємо корзину (якщо щось залишилось)
            await page.goto(f'{DD_BASE}/cart', wait_until='domcontentloaded')
            await pause(1.0, 2.0)
            clear_btn = page.locator('.cart-clear, .clear-cart, [data-action="clear-cart"]').first
            if await clear_btn.count() > 0:
                await human_click(page, '.cart-clear, .clear-cart, [data-action="clear-cart"]')
                await pause(0.5, 1.5)

            # Додаємо товари
            print('🛝 Додаємо товари до корзини DD...')
            items    = order.get('items', [])
            added    = 0
            skipped  = []

            for item in items:
                ok = await add_to_cart(page, item['sku'], item.get('qty', 1))
                if ok:
                    added += 1
                else:
                    skipped.append(item['sku'])
                # Пауза між товарами
                await pause(2.0, 5.0)

            if added == 0:
                return {'ok': False, 'error': 'Жоден товар не додано до корзини', 'skipped': skipped}

            # Заповнюємо форму
            await fill_checkout(page, order)

            # Відправляємо
            dd_order_num = await submit_order(page)

            return {
                'ok':        True,
                'dd_order':  dd_order_num,
                'added':     added,
                'skipped':   skipped,
                'timestamp': datetime.now().isoformat(),
            }

        except Exception as e:
            # Скріншот для діагностики
            shot = DATA_DIR / f'dd_error_{int(time.time())}.png'
            await page.screenshot(path=str(shot))
            print(f'❌ Помилка: {e} | Скріншот: {shot}')
            return {'ok': False, 'error': str(e), 'screenshot': str(shot)}

        finally:
            await browser.close()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python tools/dd_auto.py <orderId>')
        sys.exit(1)

    order_id = sys.argv[1]
    # Читаємо замовлення з Firestore-дампу або локального файлу
    orders_f = DATA_DIR / 'orders_dump.json'
    if not orders_f.exists():
        sys.exit(f'Файл {orders_f} не знайдено. Запустіть експорт замовлень спочатку.')

    orders = json.loads(orders_f.read_text('utf-8'))
    order  = next((o for o in orders if str(o.get('id')) == order_id), None)
    if not order:
        sys.exit(f'Замовлення {order_id} не знайдено')

    result = asyncio.run(process_order(order))
    print(json.dumps(result, ensure_ascii=False, indent=2))
