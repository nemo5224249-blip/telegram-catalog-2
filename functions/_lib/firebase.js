import { initializeApp, getApps } from 'firebase/app';
import { getFirestore } from 'firebase/firestore';
let db;
export function getDb() {
  if (db) return db;
  const app = getApps().length === 0
    ? initializeApp({ apiKey: process.env.FIREBASE_API_KEY, projectId: process.env.FIREBASE_PROJECT_ID })
    : getApps()[0];
  db = getFirestore(app);
  return db;
}