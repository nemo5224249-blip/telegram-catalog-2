// functions/_lib/mono.js
// Утиліти для роботи з Monobank API

import { webcrypto } from 'node:crypto'

/**
 * Перевіряє підпис вебхука Monobank.
 * Monobank підписує тіло запиту приватним ключем.
 * Ми перевіряємо підпис публічним ключем з env.MONO_PUB_KEY.
 *
 * Публічний ключ отримати: GET https://api.monobank.ua/api/merchant/pubkey
 * Headers: X-Token: {MONO_TOKEN}
 * Зберегти значення поля "key" в MONO_PUB_KEY (Cloudflare Secret).
 */
export async function verifyMonoSignature(body, signatureB64, pubKeyB64) {
  if (!pubKeyB64 || !signatureB64) return false

  try {
    const keyData  = Uint8Array.from(atob(pubKeyB64), c => c.charCodeAt(0))
    const sigData  = Uint8Array.from(atob(signatureB64), c => c.charCodeAt(0))
    const bodyData = new TextEncoder().encode(body)

    const pubKey = await webcrypto.subtle.importKey(
      'spki',
      keyData,
      { name: 'ECDSA', namedCurve: 'P-256' },
      false,
      ['verify']
    )

    return await webcrypto.subtle.verify(
      { name: 'ECDSA', hash: 'SHA-256' },
      pubKey,
      sigData,
      bodyData
    )
  } catch (e) {
    console.error('[mono] verifySignature error:', e)
    return false
  }
}

/**
 * Отримати статус інвойсу.
 * Корисно для перевірки вручну або при пропущеному вебхуку.
 */
export async function getInvoiceStatus(invoiceId, monoToken) {
  const resp = await fetch(
    `https://api.monobank.ua/api/merchant/invoice/status?invoiceId=${invoiceId}`,
    { headers: { 'X-Token': monoToken } }
  )
  return resp.json()
}

/**
 * Скасувати інвойс (до оплати).
 */
export async function cancelInvoice(invoiceId, monoToken) {
  const resp = await fetch('https://api.monobank.ua/api/merchant/invoice/cancel', {
    method:  'POST',
    headers: { 'X-Token': monoToken, 'Content-Type': 'application/json' },
    body:    JSON.stringify({ invoiceId }),
  })
  return resp.json()
}
