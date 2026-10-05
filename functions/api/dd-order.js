// functions/api/dd-order.js
// Тригер автоматичного оформлення замовлення на DD Tuning
// POST /api/dd-order  { orderId, mode: 'auto' | 'manual' }

import { getDb }   from '../_lib/firebase.js'
import { sendTg } from '../_lib/telegram.js'

export async function onRequestPost({ request, env }) {
  try {
    const { orderId, mode = 'auto' } = await request.json()
    if (!orderId) return Response.json({ ok: false, error: 'orderId required' }, { status: 400 })

    const db    = getDb(env)
    const snap  = await db.doc(`orders/${orderId}`).get()
    if (!snap.exists) return Response.json({ ok: false, error: 'Order not found' }, { status: 404 })

    const order = snap.data()

    // ── Ручний режим: шлемо повідомлення в Telegram + посилання ─────────────
    if (mode === 'manual') {
      const items = (order.items || []).map(
        i => `• ${i.name} (${i.sku}) ×${i.qty} — ${i.price} грн`
      ).join('\n')

      const cartData = {
        items: (order.items || []).map(i => ({
          sku: i.sku, qty: i.qty
        })),
        customer: {
          name:  order.name,
          phone: order.phone,
          email: order.email,
        },
        delivery: {
          city:      order.delivery?.city,
          warehouse: order.delivery?.warehouse,
        },
      }

      const cartB64 = Buffer.from(JSON.stringify(cartData)).toString('base64url')
      const cartUrl = `https://ddtuning.com.ua/cart.html#nft=${cartB64}`

      await sendTg(env,
        `🛒 <b>Замовлення #${orderId} — оформіть в DD</b>\n` +
        `👤 ${order.name}, ${order.phone}\n\n` +
        `${items}\n\n` +
        `<a href="${cartUrl}">→ Відкрити корзину DD</a>`
      )

      await db.doc(`orders/${orderId}`).set(
        { ddStatus: 'manual_notified', ddUpdated: new Date().toISOString() },
        { merge: true }
      )

      return Response.json({ ok: true, mode: 'manual' })
    }

    // ── Авторежим: запускаємо Playwright через зовнішній GitHub Action ───
    // Cloudflare Functions не можуть запускати Python/Playwright безпосередньо,
    // тому тригеримо GitHub Actions workflow через API.
    const ghOrg  = env.GITHUB_REPO // 'nemo5224249-blip/need-for-tuning'
    const ghTok  = env.GITHUB_TOKEN

    if (!ghOrg || !ghTok) {
      // Fallback: повертаємо у ручний режим
      return onRequestPost({ request: new Request('', {
        method: 'POST',
        body: JSON.stringify({ orderId, mode: 'manual' }),
      }), env })
    }

    const trigger = await fetch(
      `{{https://api.github.com/repos/${ghOrg}/actions/workflows/dd-auto.yml/dispatches}}`,
      {
        method: 'POST',
        headers: {
          Authorization: `token ${ghTok}`,
          'Content-Type': 'application/json',
          Accept: 'application/vnd.github.v3+json',
        },
        body: JSON.stringify({ ref: 'main', inputs: { order_id: orderId } }),
      }
    )

    if (!trigger.ok) {
      const err = await trigger.text()
      console.error('[dd-order] GitHub dispatch error:', err)
      // Fallback до ручного режиму
      await sendTg(env, `⚠️ DD авто не запустилось. Перехід у ручний режим: #${orderId}`)
    } else {
      await db.doc(`orders/${orderId}`).set(
        { ddStatus: 'auto_queued', ddUpdated: new Date().toISOString() },
        { merge: true }
      )
      await sendTg(env, `🤖 DD авто: замовлення #${orderId} в очерезі...`)
    }

    return Response.json({ ok: true, mode: 'auto' })

  } catch (e) {
    console.error('[dd-order]', e)
    return Response.json({ ok: false, error: e.message }, { status: 500 })
  }
}
