// node tests/phone_client.mjs BASE TOKEN FILE : resumable upload through viewer/sld-helper-client.js, with one injected network failure
import { SldHelperClient } from '../viewer/sld-helper-client.js';
import { readFileSync } from 'node:fs';
const [base, token, path] = process.argv.slice(2);
const bytes = readFileSync(path); const file = new File([bytes], path.split('/').pop());
const realFetch = globalThis.fetch; let puts = 0, injected = 0;
globalThis.fetch = (u, o = {}) => { if (o.method === 'PUT' && ++puts === 2) { injected++; return Promise.reject(new TypeError('network down (injected)')); } return realFetch(u, o); };
const c = new SldHelperClient({ base, token }); let last = 0;
const r = await c.uploadResumable(file, { chunk: 64 * 1024, retries: 3, onProgress: (l) => { last = l; } });
console.log(JSON.stringify({ job: r.job, injected, puts, sent: last, size: file.size }));
