// hosting/test_dirname.js
try {
    console.log("__dirname is:", __dirname);
} catch (e) {
    console.log("__dirname failed:", e.message);
}
import { fileURLToPath } from 'url';
import { dirname } from 'path';
const __filename = fileURLToPath(import.meta.url);
const __dirname_esm = dirname(__filename);
console.log("__dirname_esm is:", __dirname_esm);
