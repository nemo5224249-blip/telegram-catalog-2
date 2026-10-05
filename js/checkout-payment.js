// Додати в кінць файлу js/checkout.js (replace функцію підтвердження замовлення)

// ── Кнопка «Оплатити карткою» ────────────────────────────────────────────
// Підставити після кнопки «Накладений платіж» на сторінці оформлення

async function initMonoPayment(orderId, amount, items) {
  const btn = document.getElementById('pay-mono-btn')
  if (!btn) return

  btn.addEventListener('click', async () => {
    btn.disabled = true
    btn.textContent = 'Очікуйте...'

    try {
      const res = await fetch('/api/mono-create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ orderId, amount, items }),
      })
      const data = await res.json()

      if (data.ok && data.pageUrl) {
        // Перенаправляємо на сторінку оплати Monobank
        window.location.href = data.pageUrl
      } else {
        throw new Error(data.error || 'Невідома помилка')
      }
    } catch (e) {
      alert('Не вдалося створити платіж: ' + e.message)
      btn.disabled = false
      btn.textContent = 'Оплатити карткою (Monobank)'
    }
  })
}


// ── HTML-блок кнопки (HTML-шаблон) ────────────────────────────────────────
/*
Додати після радіокнопки «Накладений платіж» в формі оформлення:

<div class="payment-method" id="payment-mono" style="display:none">
  <button id="pay-mono-btn" class="btn btn--primary btn--pay">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
      <rect width="24" height="24" rx="4" fill="#000"/>
      <path d="M4 8h16M4 12h8" stroke="#fff" stroke-width="2" stroke-linecap="round"/>
    </svg>
    Оплатити карткою (Monobank)
  </button>
  <p class="payment-hint">Перейдете на сторінку Monobank для безпечної оплати</p>
</div>

І додати до radio-напису «Оплата»:

<label class="payment-option">
  <input type="radio" name="payment" value="card" />
  <span>💳 Оплата карткою (Monobank)</span>
</label>
*/

// Слухаємо вибір способу оплати – показуємо/ховаємо кнопку
document.querySelectorAll('input[name="payment"]').forEach(radio => {
  radio.addEventListener('change', () => {
    const monoBlock = document.getElementById('payment-mono')
    if (monoBlock) {
      monoBlock.style.display = radio.value === 'card' ? 'block' : 'none'
    }
  })
})
