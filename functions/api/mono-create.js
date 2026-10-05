// functions/api/mono-create.js
// Создаёт инвойс Monobank и возвращает ссылку на оплату

import { getDb } from '../_lib/firebase.js'

export async function onRequestPost({ request, env }) {
  try {
    const { orderId, amount, items = [] } = await request.json()

    if (!orderId || !amount) {
      return Response.json({ ok: false, error: 'orderId and amount required' }, { status: 400 })
    }

    const siteUrl = env.SITE_URL || 'https://need-for-tuning.pages.dev'

    // Формируем корзину для Monobank (отображается на странице оплаты)
    const basketOrder = items.map(item => ({
      name:  item.name,
      qty:   item.qty   || 1,
      sum:   Math.round((item.price || 0) * 100), // копійки
      unit:  'шт',
      code:  item.sku  || '',
      icon:  item.image || '',
    }))

    const payload = {
      amount:   Math.round(amount * 100), // грн → копійки
      ccy:      980,                       // UAH
      merchantPaymInfo: {
        reference:   orderId,
        destination: `Замовлення №${orderId} — Tuning Express`,
        basketOrder,
      },
      redirectUrl: `${siteUrl}/diakuiemo/?order=${orderId}`,
      webHookUrl:  `${siteUrl}/api/mono-webhook`,
      validity:    3600,        // інвойс дійсний 1 годину
      paymentType: 'debit',    // лише дебетові картки
    }

    const mono = await fetch('https://api.monobank.ua/api/merchant/invoice/create', {
      method:  'POST',
      headers: {
        'X-Token':      env.MONO_TOKEN,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    })

    if (!mono.ok) {
      const err = await mono.text()
      console.error('[mono-create] Monobank error:', err)
      return Response.json({ ok: false, error: 'Monobank error: ' + err }, { status: 502 })
    }

    const { invoiceId, pageUrl } = await mono.json()

    // Зберігаємо invoiceId в замовленні
    const db = getDb(env)
    await db.doc(`orders/${orderId}`).set(
      { monoInvoiceId: invoiceId, paymentStatus: 'pending', updatedAt: new Date().toISOString() },
      { merge: true }
    )

    return Response.json({ ok: true, invoiceId, pageUrl })

  } catch (e) {
    console.error('[mono-create]', e)
    return Response.json({ ok: false, error: e.message }, { status: 500 })
  }
}
