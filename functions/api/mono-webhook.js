// functions/api/mono-webhook.js
// Приймає колбек від Monobank після оплати

import { getDb }     from '../_lib/firebase.js'
import { sendTg }   from '../_lib/telegram.js'
import { verifyMonoSignature } from '../_lib/mono.js'

export async function onRequestPost({ request, env }) {
  try {
    const body      = await request.text()
    const signature = request.headers.get('X-Sign')

    // Перевіряємо підпис Monobank (захист від підробок)
    if (!verifyMonoSignature(body, signature, env.MONO_PUB_KEY)) {
      console.warn('[mono-webhook] Invalid signature')
      return new Response('Forbidden', { status: 403 })
    }

    const data = JSON.parse(body)
    const { invoiceId, status, amount, reference: orderId, failureReason } = data

    const db    = getDb(env)
    const order = await db.doc(`orders/${orderId}`).get()

    if (!order.exists) {
      console.error('[mono-webhook] Order not found:', orderId)
      return new Response('OK') // Повертаємо 200 щоб Mono не повторював
    }

    const orderData = order.data()

    // ── Статуси Monobank ──────────────────────────────────────────────────
    // created | processing | hold | success | failure | reversed | expired

    const statusMap = {
      success:    'paid',
      failure:    'payment_failed',
      reversed:   'refunded',
      expired:    'payment_expired',
      processing: 'payment_processing',
      hold:       'payment_hold',
    }

    const newStatus = statusMap[status] || orderData.status

    await db.doc(`orders/${orderId}`).set(
      {
        paymentStatus:  status,
        status:         newStatus,
        monoInvoiceId:  invoiceId,
        paidAmount:     status === 'success' ? amount / 100 : undefined,
        paymentUpdated: new Date().toISOString(),
        ...(failureReason ? { paymentFailReason: failureReason } : {}),
      },
      { merge: true }
    )

    // ── Telegram-сповіщення ───────────────────────────────────────────────
    if (status === 'success') {
      const name    = orderData.name  || ''
      const phone   = orderData.phone || ''
      const total   = (amount / 100).toFixed(0)
      const itemsStr = (orderData.items || []).map(
        i => `  • ${i.name} × ${i.qty} — ${i.price} грн`
      ).join('\n')

      await sendTg(
        env,
        `✅ <b>Оплачено!</b> Замовлення <b>#${orderId}</b>\n` +
        `👤 ${name}, ${phone}\n` +
        `💳 ${total} грн (Monobank)\n` +
        (itemsStr ? `\nТовари:\n${itemsStr}` : '')
      )
    } else if (status === 'failure') {
      await sendTg(
        env,
        `❌ Оплата не пройшла — замовлення <b>#${orderId}</b>\n` +
        `Причина: ${failureReason || 'невідомо'}`
      )
    }

    return new Response('OK')

  } catch (e) {
    console.error('[mono-webhook]', e)
    return new Response('Error', { status: 500 })
  }
}
