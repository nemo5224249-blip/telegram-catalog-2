const PROJECT = process.env.FIREBASE_PROJECT_ID;
const KEY = process.env.FIREBASE_API_KEY;
const BASE = `{{https://firestore.googleapis.com/v1/projects/${PROJECT}}}/databases/(default)/documents`;

function toFields(obj) {
  const f = {};
  for (const [k, v] of Object.entries(obj)) {
    if (typeof v === 'string') f[k] = { stringValue: v };
    else if (typeof v === 'number') f[k] = { integerValue: String(v) };
    else if (typeof v === 'boolean') f[k] = { booleanValue: v };
    else f[k] = { stringValue: JSON.stringify(v) };
  }
  return f;
}

export async function addDoc(collection, data) {
  const res = await fetch(`${BASE}/${collection}?key=${KEY}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fields: toFields(data) })
  });
  return res.json();
}

export function getDb() {
  return { addDoc };
}
