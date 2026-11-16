












import { verifyRfc3161Token } from "./rfc3161.js";

const GENESIS_PREV_HASH = "0".repeat(64);
const SEAL = "lifecycle.session_end";
const GENESIS = "lifecycle.session_start";
const HUMAN_OVERSIGHT = "human_oversight";
const MODEL_CALL = "model_call";
const HEARTBEAT = "lifecycle.heartbeat";
const JS_SAFE_INT_MAX = Number.MAX_SAFE_INTEGER; 
const VERIFIER_VERSION = "1.2.8";


const subtle = globalThis.crypto.subtle;

const enc = new TextEncoder();


function codePointSort(a, b) {
  
  const ai = [...a], bi = [...b];
  const n = Math.min(ai.length, bi.length);
  for (let i = 0; i < n; i++) {
    const d = ai[i].codePointAt(0) - bi[i].codePointAt(0);
    if (d !== 0) return d;
  }
  return ai.length - bi.length;
}

function hasLoneSurrogate(s) {
  
  
  
  
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xD800 && c <= 0xDBFF) {
      const n = s.charCodeAt(i + 1);
      if (!(n >= 0xDC00 && n <= 0xDFFF)) return true;
      i++;
    } else if (c >= 0xDC00 && c <= 0xDFFF) {
      return true;
    }
  }
  return false;
}

function canonicalString(s) {
  if (hasLoneSurrogate(s)) {
    throw new CanonicalError("string contains a lone surrogate (invalid Unicode; " +
                             "it cannot be encoded as UTF-8 or hashed portably)");
  }
  
  return JSON.stringify(s.normalize("NFC"));
}

export class CanonicalError extends Error {}

function canon(value) {
  
  
  
  
  
  if (value === null || value === undefined) return "null";
  const t = typeof value;
  if (t === "boolean") return value ? "true" : "false";
  if (t === "string") return canonicalString(value);
  if (t === "number") {
    if (!Number.isInteger(value)) throw new CanonicalError("floats are not allowed in records");
    if (Math.abs(value) > JS_SAFE_INT_MAX) throw new CanonicalError("integer out of safe range");
    return String(value);
  }
  if (Array.isArray(value)) return "[" + value.map(canon).join(",") + "]";
  if (t === "object") {
    
    
    
    const keys = Object.keys(value).sort(
      (a, b) => codePointSort(a.normalize("NFC"), b.normalize("NFC")));
    
    
    
    const seen = new Set();
    for (const k of keys) {
      const nk = k.normalize("NFC");
      if (seen.has(nk))
        throw new CanonicalError("object keys collide after NFC normalization: " + canonicalString(nk));
      seen.add(nk);
    }
    return "{" + keys.map(k => canonicalString(k) + ":" + canon(value[k])).join(",") + "}";
  }
  throw new CanonicalError("unsupported type " + t);
}

function hex(buf) {
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
}
function fromHex(s) {
  const a = new Uint8Array(s.length / 2);
  for (let i = 0; i < a.length; i++) a[i] = parseInt(s.substr(i * 2, 2), 16);
  return a;
}
async function sha256Hex(bytes) {
  return hex(await subtle.digest("SHA-256", bytes));
}
export async function hashValue(value) {
  return sha256Hex(enc.encode(canon(value)));
}

async function verifySig(pubkeyHex, msgBytes, sigHex) {
  try {
    const key = await subtle.importKey("raw", fromHex(pubkeyHex), { name: "Ed25519" }, false, ["verify"]);
    return await subtle.verify({ name: "Ed25519" }, key, fromHex(sigHex), msgBytes);
  } catch { return false; }
}

function recordContent(rec) {
  const out = {};
  for (const k of Object.keys(rec)) if (k !== "record_hash" && k !== "record_sig") out[k] = rec[k];
  return out;
}


async function leafHash(hHex) {
  const h = fromHex(hHex);
  const buf = new Uint8Array(1 + h.length); buf[0] = 0x00; buf.set(h, 1);
  return new Uint8Array(await subtle.digest("SHA-256", buf));
}
async function nodeHash(l, r) {
  const buf = new Uint8Array(1 + l.length + r.length);
  buf[0] = 0x01; buf.set(l, 1); buf.set(r, 1 + l.length);
  return new Uint8Array(await subtle.digest("SHA-256", buf));
}
async function merkleRoot(leavesHex) {
  if (!leavesHex.length) return "0".repeat(64);
  let level = [];
  for (const h of leavesHex) level.push(await leafHash(h));
  while (level.length > 1) {
    const nxt = [];
    for (let i = 0; i < level.length; i += 2) {
      if (i + 1 < level.length) nxt.push(await nodeHash(level[i], level[i + 1]));
      else nxt.push(level[i]);
    }
    level = nxt;
  }
  return hex(level[0]);
}

async function sha256Bytes(bytes) {
  return new Uint8Array(await subtle.digest("SHA-256", bytes));
}
function concatBytes(...parts) {
  const n = parts.reduce((s, p) => s + p.length, 0);
  const out = new Uint8Array(n);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}
function fromB64(s) {
  const bin = atob(s);
  const a = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
  return a;
}
function eqBytes(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}
async function verifyInclusionBytes(leaf, leafIndex, treeSize, proof, root) {
  if (leafIndex < 0 || treeSize < 0 || leafIndex >= treeSize) return false;
  let fn = leafIndex, sn = treeSize - 1, r = leaf;
  for (const p of proof) {
    if (p.length !== 32) return false;
    if ((fn & 1) || fn === sn) {
      r = await nodeHash(p, r);
      if (!(fn & 1)) { while (fn !== 0 && !(fn & 1)) { fn >>= 1; sn >>= 1; } }
    } else {
      r = await nodeHash(r, p);
    }
    fn >>= 1; sn >>= 1;
  }
  return sn === 0 && eqBytes(r, root);
}



const FORMAT_ERROR_CODES = new Set(["not_a_bundle", "bad_format", "malformed_bundle"]);

class Report {
  constructor() { this.findings = []; }
  add(severity, code, detail) { this.findings.push({ severity, code, detail }); }
  get ok() { return !this.findings.some(f => f.severity === "fail"); }
  
  get result() {
    const fails = this.findings.filter(f => f.severity === "fail").map(f => f.code);
    if (!fails.length) return "verified";
    if (fails.some(c => FORMAT_ERROR_CODES.has(c))) return "malformed";
    if (fails.length === 1 && fails[0] === "empty") return "empty";  
    return "tampered";
  }
  toDict() {
    return {
      ok: this.ok,
      result: this.result,
      fail: this.findings.filter(f => f.severity === "fail").length,
      warn: this.findings.filter(f => f.severity === "warn").length,
      findings: this.findings,
    };
  }
}


function deriveAxes(bundle, findings) {
  const codes = new Set((findings || []).map(f => f.code));
  const fails = (findings || []).filter(f => f.severity === "fail").map(f => f.code);
  const records = Array.isArray(bundle?.records) ? bundle.records : [];
  const anchors = Array.isArray(bundle?.anchors) ? bundle.anchors : [];
  const n = records.length;
  const integrity = n === 0 ? "unknown" : fails.length ? "fail" : "pass";
  const badTimeCodes = new Set(["anchor_sig_invalid", "anchor_decode_error", "anchor_time_mismatch",
    "anchor_imprint_mismatch", "anchor_bad_alg", "anchor_verify_error", "anchor_unknown_kind",
    "anchor_malformed"]);
  const hasRfc3161 = anchors.some(a => (a?.receipt || {}).kind === "rfc3161");
  let time;
  if ([...codes].some(c => badTimeCodes.has(c))) time = "fail";
  else if (codes.has("no_anchor") || codes.has("local_anchor_only") || codes.has("anchor_local_only")) time = "absent";
  else if (codes.has("anchor_unverified_tsa")) time = "untrusted";
  
  
  else if (codes.has("anchor_rfc3161")) time = "pass";
  else if (hasRfc3161) time = "untrusted";
  else time = "absent";
  if (integrity === "fail" && time === "pass") time = "untrusted";
  const sigRecords = records.filter(sr => sr && typeof sr === "object" && sr.record && typeof sr.record === "object" && sr.record.pubkey);
  const attribution = fails.some(c => c.includes("signature")) ? "fail"
    : n === 0 || sigRecords.length === 0 ? "absent" : "pass";
  const covered = anchors.reduce((m, a) => Math.max(m, Number(a?.covers_up_to) || 0), 0);
  const afterAnchor = codes.has("recv_after_anchor") || codes.has("ts_after_anchor");
  const coverage = n && covered >= n && !afterAnchor ? "complete"
    : covered > 0 || afterAnchor ? "partial" : "none";
  return { integrity, time, attribution, coverage };
}


export function resultWord(rep) {
  const a = rep.axes || {};
  if (rep.result === "malformed") return "NOT A PROVELOG BUNDLE";
  if (rep.result === "empty") return "NO RECORDS";
  if (a.integrity === "fail") return "TAMPERING DETECTED";
  if (a.integrity === "pass" && (a.attribution === "pass" || a.attribution === "absent")
      && a.time === "pass") {
    if (a.coverage === "complete") return "VERIFIED";
    if (a.coverage === "partial") return "VERIFIED (partial timestamp coverage)";
  }
  return "INVALID";
}

export function axesText(rep, bundle) {
  const a = rep.axes || {};
  const records = Array.isArray(bundle?.records) ? bundle.records : [];
  const anchors = Array.isArray(bundle?.anchors) ? bundle.anchors : [];
  const n = records.length;
  const covered = anchors.reduce((m, x) => Math.max(m, Number(x?.covers_up_to) || 0), 0);
  const integrity = a.integrity === "pass"
    ? (a.attribution === "pass" ? `${n} records, hash chain and signatures verified`
      : `${n} records, hash chain verified`)
    : a.integrity === "fail" ? "integrity failure; do not rely on this file"
    : "not enough data to judge integrity";
  const time = {pass: "RFC 3161 timestamp verified", absent: "no timestamp in this bundle",
    untrusted: "timestamp present, TSA not in the recognized list (Sectigo, FreeTSA)",
    fail: "timestamp invalid"}[a.time]
    || "no timestamp in this bundle";
  const attribution = {pass: "device signature verified", absent: "no device signature",
    fail: "device signature invalid"}[a.attribution] || "no device signature";
  let coverageValue = a.coverage;
  let coverage = {complete: `${covered} / ${n} records covered by a timestamp`,
    partial: `${covered} / ${n} records covered by a timestamp`,
    none: `0 / ${n} records covered by a timestamp`}[a.coverage]
    || `0 / ${n} records covered by a timestamp`;
  if (covered > 0 && a.time !== "pass") {
    coverage += {untrusted: " (unrecognized TSA)", absent: " (time not proven)",
      fail: " (timestamp invalid)"}[a.time] || " (time not proven)";
  }
  if (a.integrity === "fail") {
    
    
    coverageValue = "n/a";
    coverage = "not applicable (integrity failed)";
  }
  const row = (label, value, detail) =>
    label.padEnd(12) + String(value).toUpperCase().padEnd(10) + detail;
  let conclusion;
  if (a.integrity === "fail") conclusion = "Do not rely on this record.";
  else if (a.integrity !== "pass") conclusion = "No integrity verdict was reached.";
  else if (a.time === "untrusted") conclusion = "Evidence pack invalid: the timestamp authority is not in the recognized list (Sectigo, FreeTSA)";
  else if (a.time !== "pass") conclusion = "Evidence pack invalid: no recognizable timestamp was detected.";
  else if (a.time === "pass" && a.coverage === "complete") conclusion = a.attribution === "pass"
    ? "Record intact; its time is independently proven."
    : "Record intact; its time is independently proven, but attribution is not established.";
  else conclusion = "Record intact; its time is not fully proven.";
  return [row("Integrity", a.integrity, integrity),
    row("Time", a.time, time),
    row("Attribution", a.attribution, attribution),
    row("Coverage", coverageValue, coverage),
    `Conclusion: ${conclusion}`].join("\n");
}

export async function verifyBundle(bundle, packFiles) {
  const required = String(bundle?.verifier_min || "");
  if (required) {
    const req = required.split(".").map(x => Number.parseInt(x, 10) || 0);
    const cur = VERIFIER_VERSION.split(".").map(x => Number.parseInt(x, 10) || 0);
    const n = Math.max(req.length, cur.length);
    const newer = Array.from({length: n}, (_, i) => (req[i] || 0) - (cur[i] || 0));
    if (newer.find(x => x !== 0) > 0) {
      const up = {
        ok: false, result: "upgrade_required", fail: 0, warn: 0,
        findings: [{severity: "info", code: "upgrade_required",
          detail: `This bundle requires verifier ${required} (current ${VERIFIER_VERSION}).`}],
        axes: {integrity: "unknown", time: "absent", attribution: "absent", coverage: "none"},
      };
      up.axes_text = axesText(up, bundle);
      return up;
    }
  }
  try {
    const rep = await _verifyBundle(bundle, packFiles);
    rep.axes = deriveAxes(bundle, rep.findings);
    if (rep.axes.integrity === "pass" && rep.axes.attribution === "absent") {
      rep.add("warn", "no_device_signature",
        "no device signature present: the timestamp can prove the records existed by that time, " +
        "but attribution is not established");
    }
    rep.axes_text = axesText(rep, bundle);
    rep.verdict = resultWord(rep);
    return rep;
  } catch (err) {
    const rep = new Report();
    if (err instanceof CanonicalError) {
      rep.add("fail", "not_canonicalizable",
        `a record in this bundle cannot be canonicalized, so no hash over it can be checked and ` +
        `nothing in it can be verified: ${err.message}`);
    } else if (err instanceof TypeError || err instanceof RangeError || err instanceof SyntaxError) {
      rep.add("fail", "malformed_bundle",
        `this bundle is structurally invalid and could not be verified ` +
        `(${err.constructor.name}: ${err.message})`);
    } else {
      throw err;
    }
    const out = rep.toDict();
    out.axes = deriveAxes(bundle, out.findings);
    out.axes_text = axesText(out, bundle);
    out.verdict = resultWord(out);
    return out;
  }
}













function structuralDefect(bundle) {
  const typeName = v => v === null ? "null" : Array.isArray(v) ? "list" :
    ({ string: "str", number: "int", boolean: "bool", object: "dict" })[typeof v] || typeof v;
  for (const key of ["records", "anchors"]) {
    const value = bundle[key];
    if (value === null || value === undefined) continue;
    if (!Array.isArray(value)) return `'${key}' must be a list, got ${typeName(value)}`;
    for (let i = 0; i < value.length; i++) {
      const item = value[i];
      if (item === null || typeof item !== "object" || Array.isArray(item)) {
        return `'${key}[${i}]' must be an object, got ${typeName(item)}`;
      }
    }
  }
  const records = Array.isArray(bundle.records) ? bundle.records : [];
  for (let i = 0; i < records.length; i++) {
    const rec = records[i].record;
    if (rec !== null && rec !== undefined && (typeof rec !== "object" || Array.isArray(rec))) {
      return `'records[${i}].record' must be an object, got ${typeName(rec)}`;
    }
    if (rec && typeof rec === "object" && !Array.isArray(rec)) {
      const seq = rec.seq;
      if (seq !== null && seq !== undefined &&
          (typeof seq !== "number" || !Number.isInteger(seq))) {
        return `'records[${i}].record.seq' must be an integer, got ${typeName(seq)}`;
      }
    }
  }
  const anchors = Array.isArray(bundle.anchors) ? bundle.anchors : [];
  for (let i = 0; i < anchors.length; i++) {
    const a = anchors[i];
    const receipt = a.receipt;
    if (receipt !== null && receipt !== undefined &&
        (typeof receipt !== "object" || Array.isArray(receipt))) {
      return `'anchors[${i}].receipt' must be an object, got ${typeName(receipt)}`;
    }
    const covers = a.covers_up_to;
    if (covers !== null && covers !== undefined &&
        (typeof covers !== "number" || !Number.isInteger(covers))) {
      return `'anchors[${i}].covers_up_to' must be an integer, got ${typeName(covers)}`;
    }
    const seq = a.anchor_seq;
    if (seq !== null && seq !== undefined &&
        (typeof seq !== "number" || !Number.isInteger(seq))) {
      return `'anchors[${i}].anchor_seq' must be an integer, got ${typeName(seq)}`;
    }
  }
  return null;
}

async function _verifyBundle(bundle, packFiles) {
  const rep = new Report();
  if (bundle === null || typeof bundle !== "object" || Array.isArray(bundle)) {
    rep.add("fail", "not_a_bundle",
      `this file is valid JSON but ${Array.isArray(bundle) ? "an array" : "a " + typeof bundle}, not a ProveLog bundle (a JSON object). Check you pointed at the right file.`);
    return rep.toDict();
  }
  
  
  if (bundle.format !== "provelog.bundle/1" && bundle.format !== "flightrecorder.bundle/1") {
    rep.add("fail", "bad_format", `unexpected bundle format ${bundle.format}`);
    return rep.toDict();
  }
  const shape = structuralDefect(bundle);
  if (shape !== null) { rep.add("fail", "malformed_bundle", shape); return rep.toDict(); }

  const server = bundle.records || [];
  if (!server.length) { rep.add("fail", "empty", "bundle has no records"); return rep.toDict(); }


  
  let prev = GENESIS_PREV_HASH;
  const srh = [];
  for (let i = 0; i < server.length; i++) {
    const sr = server[i];
    if (sr.recv_seq !== i) rep.add("fail", "recv_gap", `recv_seq gap at index ${i}`);
    const recvHash = await hashValue(sr.record);
    if (recvHash !== sr.recv_hash) rep.add("fail", "recv_hash_mismatch", `recv_seq ${sr.recv_seq}: bytes do not match recv_hash`);
    const link = await hashValue({ recv_seq: sr.recv_seq, recv_ts: sr.recv_ts, recv_hash: recvHash, server_prev_hash: prev });
    if (link !== sr.server_record_hash) rep.add("fail", "server_chain_break", `recv_seq ${sr.recv_seq}: server receipt chain broken`);
    srh.push(link);
    prev = link;
  }

  
  
  
  const head = bundle.server_head;
  if (head && typeof head === "object" && !Array.isArray(head) && srh.length) {
    const lastSeq = server[server.length - 1].recv_seq;
    if (head.server_record_hash !== srh[srh.length - 1]) {
      rep.add("fail", "server_head_mismatch",
        "server_head does not match the recomputed tail of the receipt chain");
    } else if (head.recv_seq !== lastSeq) {
      rep.add("fail", "server_head_mismatch",
        `server_head claims recv_seq ${head.recv_seq}, but the chain ends at ${lastSeq}`);
    }
  }

  
  
  const bySid = new Map();
  for (const sr of server) {
    const sid = sr.record.session_id;
    if (!bySid.has(sid)) bySid.set(sid, []);
    bySid.get(sid).push(sr.record);
  }
  const sessions = [...bySid.values()].map(s => s.slice().sort((a, b) => (a.seq ?? 1e18) - (b.seq ?? 1e18)));
  const multi = sessions.length > 1;
  for (const sess of sessions) {
    const label = multi ? `session ${sess[0].session_id}: ` : "";
    await verifyClientChain(sess, rep, label);
  }
  const client = sessions.flat();

  
  for (const [sid, recs] of bySid) {
    const label = multi ? `session ${sid}: ` : "";
    const seqs = recs.map(r => r.seq);
    if (JSON.stringify(seqs) !== JSON.stringify([...seqs].sort((a, b) => a - b)))
      rep.add("fail", "arrival_reorder", `${label}records did not arrive in emission order`);
    if (new Set(seqs).size !== seqs.length) rep.add("fail", "duplicate_seq", `${label}duplicate client seq values`);
  }

  
  const anchors = bundle.anchors || [];
  if (!anchors.length) rep.add("warn", "no_anchor", "no external anchor present");
  else if (anchors.every(a => ["local", "aggregate-local"].includes((a.receipt || {}).kind)))
    rep.add("warn", "local_anchor_only", "only LOCAL anchors present; no independent third-party time proof");
  for (const a of anchors) {
    const receipt = a.receipt || {};
    const covers = a.covers_up_to ?? -1;
    const leaves = server.filter(sr => sr.recv_seq < covers && sr.recv_seq < srh.length).map(sr => srh[sr.recv_seq]);
    const root = await merkleRoot(leaves);
    if (receipt.kind === "aggregate" || receipt.kind === "aggregate-local") {
      
      await verifyAggregateReceipt(receipt, root, rep, a.anchor_seq);
      continue;
    }
    if (root !== receipt.merkle_root) { rep.add("fail", "anchor_root_mismatch", `anchor ${a.anchor_seq}: Merkle root does not cover the receipt chain`); continue; }
    if (receipt.kind === "local") {
      const ok = await verifySig(receipt.anchor_pubkey || "", enc.encode((receipt.merkle_root || "") + "|" + (receipt.gen_time || "")), receipt.signature || "");
      if (!ok) rep.add("fail", "anchor_bad_sig", `anchor ${a.anchor_seq}: local anchor signature invalid`);
      else rep.add("warn", "anchor_local_only", `anchor ${a.anchor_seq}: LOCAL anchor only, time is self-asserted`);
    } else if (receipt.kind === "rfc3161") {
      await verifyRfc3161Receipt(receipt, root, rep, a.anchor_seq);
    } else {
      rep.add("fail", "anchor_unknown_kind", `anchor ${a.anchor_seq}: unknown anchor kind`);
    }
  }


  
  
  const nHeartbeats = client.filter(r => r.action_type === HEARTBEAT).length;
  rep.add("info", "summary",
    `${(bundle.records || []).length} records, ${(bundle.anchors || []).length} anchors, ${nHeartbeats} heartbeats. Completeness is never claimed by this tool.`);

  
  for (const sess of sessions) {
    if (!sess.length || sess[sess.length - 1].action_type !== SEAL) {
      const label = multi && sess.length ? `session ${sess[0].session_id}: ` : "";
      rep.add("warn", "unsealed", `${label}session is not sealed; completeness unknown`);
    }
  }
  await _verifyAttachments(bundle, packFiles, rep);
  return rep.toDict();
}

function _hexDigest(value) {
  if (typeof value !== "string") return null;
  let s = value.trim();
  if (s.startsWith("sha256:")) s = s.slice(7);
  if (s.length !== 64 || !/^[0-9a-fA-F]+$/.test(s)) return null;
  return s.toLowerCase();
}

function _isAttachmentPath(name) {
  return /(^|\/)attachments\//.test(String(name));
}

function _attachmentDigests(bundle) {
  const digests = new Set();
  const visit = (node) => {
    if (Array.isArray(node)) { for (const c of node) visit(c); return; }
    if (!node || typeof node !== "object") return;
    if (node.attachment && typeof node.attachment === "object") {
      const d = _hexDigest(node.attachment.attachmentId || node.attachment.sha256);
      if (d) digests.add(d);
    }
    const d = _hexDigest(node.attachmentId || node.sha256);
    if (d) digests.add(d);
    for (const key of ["content", "parts", "message", "item", "data"]) {
      const child = node[key];
      if (Array.isArray(child) || (child && typeof child === "object")) visit(child);
    }
  };
  for (const sr of bundle.records || []) {
    const rec = sr.record || {};
    visit(rec.payload);
  }
  return digests;
}

async function _verifyAttachments(bundle, packFiles, rep) {
  const declared = (bundle.attachments || []).filter(a => a && typeof a === "object");
  const digests = _attachmentDigests(bundle);
  if (!declared.length && !digests.size) return;

  if (!packFiles) {
    rep.add("warn", "attachment_not_included",
      "the bundle references attachments but no files were provided, so attachment contents were not verified");
    return;
  }

  const entries = packFiles instanceof Map
    ? [...packFiles.entries()] : Object.entries(packFiles);
  const byName = new Map();
  const byHash = new Map();
  for (const [name, data] of entries) {
    const base = String(name).split("/").pop();
    const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
    byName.set(base, bytes);
    byHash.set(await sha256Hex(bytes), bytes);
  }

  const declaredDigests = new Set();
  const declaredNames = new Set();
  for (const a of declared) {
    const d = _hexDigest(a.sha256 || a.attachmentId);
    const name = String(a.path || a.file || "").split("/").pop();
    if (name) declaredNames.add(name);
    if (!d) {
      
      
      
      rep.add("warn", "attachment_entry_missing_digest",
        `attachment entry ${a.path || a.file || "?"} has no parseable sha256 digest and was not checked`);
      continue;
    }
    declaredDigests.add(d);
    if (!digests.has(d)) {
      
      
      
      
      rep.add("warn", "attachment_unreferenced_declaration",
        `attachment ${d} is declared in the manifest but no signed record references it; it is outside the signed scope`);
    }
    let data = name ? byName.get(name) : undefined;
    if (data === undefined && byHash.has(d)) data = byHash.get(d);
    if (data === undefined) {
      
      
      rep.add("warn", "attachment_missing",
        `attachment ${d} is declared but its file is not in the pack; the attachment could not be verified`);
      continue;
    }
    const actual = await sha256Hex(data);
    if (actual !== d) {
      rep.add("fail", "attachment_hash_mismatch", `attachment ${d} hashes to ${actual}, not the declared digest`);
    }
    const size = a.bytes;
    if (size !== null && size !== undefined && Number.isInteger(size) && size !== data.length) {
      rep.add("fail", "attachment_size_mismatch", `attachment ${d} is ${data.length} bytes, not the declared ${size}`);
    }
  }

  for (const d of [...digests].sort()) {
    if (declaredDigests.has(d)) continue;
    if (byHash.has(d)) continue;
    
    
    
    rep.add("warn", "attachment_reference_missing",
      `record references attachment ${d} which is not included in the pack; the attachment could not be verified`);
  }

  
  
  
  
  for (const [name, data] of entries) {
    if (!_isAttachmentPath(name)) continue;
    const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
    const h = await sha256Hex(bytes);
    const base = String(name).split("/").pop();
    
    
    
    
    
    const stem = base.includes(".") ? base.split(".")[0] : base;
    if (/^[0-9a-f]{64}$/.test(stem) && h !== stem) {
      rep.add("fail", "attachment_name_hash_mismatch",
        `attachment ${base} is named after digest ${stem} but its content hashes to ${h}`);
    }
    if (digests.has(h) || declaredDigests.has(h) || declaredNames.has(base)) continue;
    rep.add("fail", "attachment_unreferenced_file",
      `the pack contains attachment ${base} that no signed record references and no manifest entry declares`);
  }
}

async function verifyClientChain(records, rep, label = "") {
  if (!records.length) { rep.add("fail", "client_empty", `${label}no records`); return; }
  let expectedPrev = GENESIS_PREV_HASH, expectedSeq = 0;
  const pub0 = records[0].pubkey;
  const hashes = [];
  for (const rec of records) {
    const content = recordContent(rec);
    const canonBytes = enc.encode(canon(content));
    const recomputed = await sha256Hex(canonBytes);
    if (recomputed !== rec.record_hash) rep.add("fail", "client_hash_mismatch", `${label}seq=${rec.seq}: record_hash does not match content`);
    if (!await verifySig(rec.pubkey || "", canonBytes, rec.record_sig || "")) rep.add("fail", "client_bad_signature", `${label}seq=${rec.seq}: signature invalid`);
    if (rec.pubkey !== pub0) rep.add("fail", "client_key_changed", `${label}seq=${rec.seq}: device pubkey changed mid-stream`);
    if (rec.seq !== expectedSeq) rep.add("fail", "client_seq_gap", `${label}expected seq ${expectedSeq}, got ${rec.seq}`);
    if (rec.prev_hash !== expectedPrev) rep.add("fail", "client_broken_link", `${label}seq=${rec.seq}: prev_hash does not match previous record_hash`);
    expectedPrev = rec.record_hash;
    expectedSeq = (Number.isInteger(rec.seq) ? rec.seq : expectedSeq) + 1;
    hashes.push(rec.record_hash);
  }
  if (records[0].action_type !== GENESIS) rep.add("fail", "client_no_genesis", `${label}first record is not a session_start`);
  const seals = records.filter(r => r.action_type === SEAL);
  if (seals.length) {
    const seal = seals[seals.length - 1];
    if (Number.isInteger(seal.seq)) {
      const covered = records.filter(r => Number.isInteger(r.seq) && r.seq < seal.seq).map(r => r.record_hash);
      const expect = await hashValue(covered);
      if ((seal.payload || {}).session_hash !== expect) rep.add("fail", "client_bad_session_hash", `${label}seal session_hash does not cover the record set`);
    }
  }
}






async function verifyRfc3161Receipt(receipt, expectedRoot, rep, seq) {
  try {
    const info = await verifyRfc3161Token(
      receipt.token_b64 || "",
      expectedRoot,
      receipt.tsa_url || "",
    );
    const claimed = String(receipt.gen_time || "");
    if (claimed) {
      const a = Date.parse(claimed);
      const b = Date.parse(info.genTime);
      if (!Number.isFinite(a) || !Number.isFinite(b) || Math.abs(a - b) > 1) {
        rep.add("fail", "anchor_time_mismatch",
          `anchor ${seq}: the receipt says ${claimed}, but the TSA-signed token says ${info.genTime}`);
        return "fail";
      }
    }
    rep.add("info", "anchor_rfc3161",
      `anchor ${seq}: RFC3161 trusted timestamp from ${info.tsa || receipt.tsa_url} at ${info.genTime}, CMS signature and certificate chain validated offline`);
    return "trusted";
  } catch (e) {
    const message = e && e.message ? e.message : String(e);
    
    
    
    
    if (/no pinned trust root/i.test(message)) {
      rep.add("warn", "anchor_unverified_tsa",
        `anchor ${seq}: imprint matches but TSA '${receipt.tsa_url || ""}' is not in ProveLog's recognized TSA list (Sectigo, FreeTSA), so the TSA signature was not validated`);
      return "untrusted";
    }
    rep.add("fail", "anchor_rfc3161_invalid",
      `anchor ${seq}: RFC3161 token did not verify: ${message}`);
    return "fail";
  }
}






const AGG_LEAF_DOMAIN = enc.encode("provelog.ai/v1/agg-leaf:");

async function verifyAggregateReceipt(receipt, streamRoot, rep, seq) {
  const epoch = receipt.epoch || {};
  const aggRoot = String(receipt.agg_root || "");
  let commitHex;
  try {
    const commit = await sha256Bytes(enc.encode(canon({
      stream_id: receipt.stream_id || "",
      anchor_seq: epoch.index ?? 0,
      covers_up_to: receipt.covers_up_to ?? 0,
      merkle_root: streamRoot,
      gen_time: epoch.started_at || "",
      kind: "aggregate",
      token_b64_or_sig: "",
    })));
    commitHex = hex(commit);
  } catch (e) {
    rep.add("fail", "agg_commitment_mismatch", `anchor ${seq}: cannot recompute the aggregate commitment: ${e.message}`);
    return "fail";
  }
  if (commitHex !== receipt.commitment) {
    rep.add("fail", "agg_commitment_mismatch", `anchor ${seq}: the aggregate commitment does not bind this stream's root/coverage/epoch (receipt substitution)`);
    return "fail";
  }
  
  const leafIndex = receipt.leaf_index ?? -1;
  const size = epoch.size ?? 0;
  if (leafIndex < 0 || leafIndex >= size) {
    rep.add("fail", "agg_inclusion_invalid", `anchor ${seq}: leaf_index missing or out of range`);
    return "fail";
  }
  const leaf = new Uint8Array(await subtle.digest(
    "SHA-256", concatBytes(new Uint8Array([0x00]), AGG_LEAF_DOMAIN, fromHex(commitHex))));
  let proof;
  try { proof = (receipt.inclusion_proof || []).map(fromB64); }
  catch { rep.add("fail", "agg_inclusion_invalid", `anchor ${seq}: malformed aggregate proof`); return "fail"; }
  if (!await verifyInclusionBytes(leaf, leafIndex, size, proof, fromHex(aggRoot))) {
    rep.add("fail", "agg_root_mismatch", `anchor ${seq}: the inclusion proof does not fold to the aggregate root`);
    return "fail";
  }
  const aggReceipt = receipt.agg_receipt || {};
  if (aggReceipt.merkle_root !== aggRoot) {
    rep.add("fail", "agg_root_mismatch", `anchor ${seq}: the shared stamp covers a different root than the aggregate root it is presented with`);
    return "fail";
  }
  
  if (aggReceipt.kind === "rfc3161") {
    return await verifyRfc3161Receipt(aggReceipt, aggRoot, rep, seq);
  }
  if (aggReceipt.kind === "local") {
    const ok = await verifySig(aggReceipt.anchor_pubkey || "",
                               enc.encode((aggReceipt.merkle_root || "") + "|" + (aggReceipt.gen_time || "")),
                               aggReceipt.signature || "");
    if (!ok) { rep.add("fail", "anchor_bad_sig", `anchor ${seq}: aggregate-local stamp signature invalid`); return "fail"; }
    rep.add("warn", "anchor_local_only", `anchor ${seq}: aggregate receipt under a LOCAL stamp only, time is self-asserted`);
    return "untrusted";
  }
  rep.add("fail", "anchor_unknown_kind", `anchor ${seq}: unknown aggregate stamp kind ${aggReceipt.kind}`);
  return "fail";
}








export const DOC_URL = "https://provelog.ai/doc";

const TSA_NAMES = {
  "timestamp.sectigo.com": "Sectigo",
  "timestamp.digicert.com": "DigiCert",
  "timestamp.certum.pl": "Certum",
  "freetsa.org": "FreeTSA",
};

function tsaLabel(tsaUrl) {
  const host = String(tsaUrl || "").split("://").pop().split("/")[0];
  return TSA_NAMES[host] || host || "unknown TSA";
}

function fmtStampTime(genTime) {
  const t = String(genTime || "").slice(0, 16).replace("T", " ");
  return t.length === 16 ? `${t} UTC` : (genTime || "unknown");
}

export function factSentence(bundle, fileName, tsaSignatureVerified) {
  const records = (bundle && bundle.records) || [];
  const n = records.length;
  const rfc = ((bundle && bundle.anchors) || [])
    .filter(a => a && a.receipt &&
      (a.receipt.kind === "rfc3161" ||
       (a.receipt.kind === "aggregate" &&
        (a.receipt.agg_receipt || {}).kind === "rfc3161")))
    .map(a => a.receipt.kind === "rfc3161"
      ? { gen_time: a.receipt.gen_time || "", tsa_url: a.receipt.tsa_url || "",
          covers_up_to: a.covers_up_to ?? -1 }
      : { gen_time: (a.receipt.agg_receipt || {}).gen_time || "",
          tsa_url: (a.receipt.agg_receipt || {}).tsa_url || "",
          covers_up_to: a.covers_up_to ?? -1 });
  const head = `Offline verification: all ${n} records in "${fileName}" match the hash chain;`;
  if (!rfc.length) {
    return head + " this file carries no third-party timestamp, so the record " +
           "time is self-asserted by this machine, and holds only if the local " +
           "computing environment is trusted. See " + DOC_URL;
  }
  const counts = new Map();
  for (const st of rfc) {
    const name = tsaLabel(st.tsa_url);
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  
  
  const gt = (a, b) => String(a.gen_time || "") < String(b.gen_time || "");
  const ordered = [...rfc].sort((x, y) => (gt(x, y) ? -1 : (gt(y, x) ? 1 : 0)));
  const first = ordered[0], last = ordered[rfc.length - 1];
  const t1 = fmtStampTime(first.gen_time), t2 = fmtStampTime(last.gen_time);
  const n1 = tsaLabel(first.tsa_url), n2 = tsaLabel(last.tsa_url);
  const k = rfc.length;
  const maxCover = Math.max(...rfc.map(a => (a.covers_up_to ?? -1)));
  const tail = records.filter(r => (r.recv_seq ?? 0) >= maxCover).length;
  const tailPart = tail > 0
    ? `; ${tail} more records arrived after the last stamp and are not yet covered by a third-party timestamp`
    : "";
  const groups = [...counts.entries()].map(([name, c]) => `${name} ${c}`).join(", ");
  if (tsaSignatureVerified) {
    const ts = counts.size === 1
      ? ` the chain root is covered by ${k} RFC 3161 timestamp(s) from ${n1} (first stamp ${t1}, last stamp ${t2})`
      : ` the chain root is covered by ${k} RFC 3161 timestamp(s) (${groups}), first stamp ${t1} (${n1}), last stamp ${t2} (${n2})`;
    return head + ts + tailPart + ". See " + DOC_URL;
  }
  const unchecked = "; the timestamp signatures were not checked on this page - use the command-line tool for the full check.";
  const ts = counts.size === 1
    ? ` detected ${k} RFC 3161 timestamp(s) from ${n1} (first stamp ${t1}, last stamp ${t2})`
    : ` detected ${k} RFC 3161 timestamp(s) (${groups}), first stamp ${t1} (${n1}), last stamp ${t2} (${n2})`;
  return head + ts + tailPart + unchecked;
}






async function _inflateRaw(bytes) {
  const ds = new DecompressionStream("deflate-raw");
  const stream = new Blob([bytes]).stream().pipeThrough(ds);
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export async function unzipEntries(input) {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let eocd = -1;
  const floor = Math.max(0, bytes.length - 22 - 65535);
  for (let i = bytes.length - 22; i >= floor; i--) {
    if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error("not a ZIP file");
  const count = dv.getUint16(eocd + 10, true);
  let p = dv.getUint32(eocd + 16, true);
  const out = [];
  for (let i = 0; i < count; i++) {
    if (dv.getUint32(p, true) !== 0x02014b50) throw new Error("bad ZIP directory");
    const method = dv.getUint16(p + 10, true);
    const csize = dv.getUint32(p + 20, true);
    const nameLen = dv.getUint16(p + 28, true);
    const extraLen = dv.getUint16(p + 30, true);
    const commentLen = dv.getUint16(p + 32, true);
    const localOff = dv.getUint32(p + 42, true);
    const name = new TextDecoder().decode(bytes.subarray(p + 46, p + 46 + nameLen));
    if (dv.getUint32(localOff, true) !== 0x04034b50) throw new Error("bad ZIP member");
    const lName = dv.getUint16(localOff + 26, true);
    const lExtra = dv.getUint16(localOff + 28, true);
    const start = localOff + 30 + lName + lExtra;
    const comp = bytes.slice(start, start + csize);
    let data;
    if (method === 0) data = comp;
    else if (method === 8) data = await _inflateRaw(comp);
    else throw new Error(`unsupported ZIP method ${method}`);
    out.push({ name, data });
    p += 46 + nameLen + extraLen + commentLen;
  }
  return out;
}

export default { verifyBundle, unzipEntries, CanonicalError };
