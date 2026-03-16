import { initializeApp } from "firebase/app";
import { getDatabase } from "firebase/database";

const firebaseConfig = {
  apiKey: "AIzaSyBd831rtqRM7ZEU7LnY1LRDp6Oin4HhTSo",
  authDomain: "rokeysmarthub.firebaseapp.com",
  databaseURL: "https://rokeysmarthub-default-rtdb.asia-southeast1.firebasedatabase.app",
  projectId: "rokeysmarthub",
  storageBucket: "rokeysmarthub.firebasestorage.app",
  messagingSenderId: "666949886270",
  appId: "1:666949886270:web:59bcda7afc1bcb5280cc6f"
};

// Initialize Firebase
const app = initializeApp(firebaseConfig);
export const db = getDatabase(app);