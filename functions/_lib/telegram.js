const API = `{{https://api.telegram.org/bot${process.env.TELEGRAM_BOT_TOKEN}}}`;
export async function sendTg(chatId, text, opts = {}) {
  return fetch(`${API}/sendMessage`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: chatId, text, parse_mode: 'HTML', ...opts })
  }).then(r => r.json());
}