// 富途 OpenAPI REST 客户端（Ed25519 签名）—— 唯一签名/请求出口
//
// 文档：https://open.futunn.com/zh-cn/api/overview/getting-started
// 签名原文（5 段，\n 连接；空段也要保留分隔符）：
//   {timestamp_ms}\n{HTTP_METHOD}\n{request_path}\n{query_string}\n{body_sha256_hex}
// Ed25519 直接对原文签名，结果 Base64 放 Authorization（**不加** Bearer 前缀）。
//
// 凭据（刻意放在仓库之外，避免误提交）：
//   ~/.config/futu/private_key.pem   Ed25519 私钥（chmod 600）
//   ~/.config/futu/appkey            AppKey ID（chmod 600）
//
// 用法：
//   node futu-client.mjs selftest                    单次冒烟测试
//   node futu-client.mjs get <path> [k=v ...]        任意 GET（探测接口用）
//   node futu-client.mjs kline <symbol> <end>        拉一次历史 K 线
import { createPrivateKey, sign, createHash, randomBytes } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

export const BASE = 'https://webapi.futunn.com';
const CFG_DIR = join(homedir(), '.config', 'futu');

const privateKey = createPrivateKey(readFileSync(join(CFG_DIR, 'private_key.pem')));
const appKeyId = readFileSync(join(CFG_DIR, 'appkey'), 'utf8').trim();

// 服务端时间 - 本机时间（毫秒）。富途要求偏移 <= 5s，否则报 -12006。
let clockOffsetMs = 0;

/** 用 /server-time 校准时钟（无需鉴权） */
export async function syncTime() {
  const t0 = Date.now();
  const res = await fetch(`${BASE}/api/v1.0/server-time`);
  const json = await res.json();
  const t1 = Date.now();
  clockOffsetMs = Number(json.server_time_ms) - Math.round((t0 + t1) / 2);
  return clockOffsetMs;
}

function buildSignString(tsMs, method, path, query, body) {
  const bodyHash = body ? createHash('sha256').update(body).digest('hex') : '';
  return `${tsMs}\n${method}\n${path}\n${query}\n${bodyHash}`;
}

/**
 * GET 请求。params 序列化出的 query 与签名原文共用同一个字符串，保证二者一致
 * （签名必须基于"最终发出的请求"，顺序/编码不一致会验签失败）。
 */
export async function apiGet(path, params = {}, { retries = 4 } = {}) {
  const query = new URLSearchParams(params).toString();

  for (let attempt = 0; attempt <= retries; attempt++) {
    const ts = String(Date.now() + clockOffsetMs);
    const nonce = randomBytes(8).toString('hex');
    const signature = sign(null, Buffer.from(buildSignString(ts, 'GET', path, query, '')), privateKey).toString('base64');

    const res = await fetch(`${BASE}${path}${query ? '?' + query : ''}`, {
      headers: {
        'X-Api-Key': appKeyId,
        'X-Timestamp': ts,
        'X-Nonce': nonce,
        Authorization: signature,
      },
    });

    const text = await res.text();
    let json;
    try {
      json = JSON.parse(text);
    } catch {
      json = { ret_code: -999, ret_msg: 'non-json response', _raw: text.slice(0, 300) };
    }

    // 限流有两种表现：HTTP 429，或 HTTP 200 + ret_code -11（rate limit exceeded）。
    // 服务端会在 error.message 里给 "retry after Ns"，优先按它等待；其余一律不重试。
    const rateLimited = res.status === 429 || json.ret_code === -11;
    if (rateLimited && attempt < retries) {
      const fromHeader = Number(res.headers.get('retry-after') ?? 0) * 1000;
      const m = /retry after (\d+)/.exec(json.error?.message ?? '');
      const wait = fromHeader || (m ? Number(m[1]) * 1000 : 2 ** attempt * 3000);
      console.error(`  ⏳ 限流，等待 ${Math.round(wait / 1000)}s 后重试 (${attempt + 1}/${retries})`);
      await new Promise((r) => setTimeout(r, wait + 1000));
      continue;
    }

    return { httpStatus: res.status, ...json };
  }
}

/** 历史 K 线。ktype: 1=1分 2=日 3=周 4=月；autype: 0=不复权 1=前复权 2=后复权 */
export function historyKline(symbol, { end, start, ktype = 2, autype = 1, num = 370 } = {}) {
  const params = { end, ktype: String(ktype), autype: String(autype), num: String(num) };
  if (start) params.start = start;
  return apiGet(`/api/v1.0/quote/${symbol}/history-kline`, params);
}

/**
 * POST 请求（JSON body）。
 * 签名原文的 body_part = **实际发出的 body 字节**的 SHA256 小写十六进制，
 * 所以必须先定 body 字符串再签名，中途不能重新格式化 JSON。
 */
export async function apiPost(path, bodyObj, { retries = 4 } = {}) {
  const body = JSON.stringify(bodyObj);
  const query = '';

  for (let attempt = 0; attempt <= retries; attempt++) {
    const ts = String(Date.now() + clockOffsetMs);
    const nonce = randomBytes(8).toString('hex');
    const signature = sign(null, Buffer.from(buildSignString(ts, 'POST', path, query, body)), privateKey).toString('base64');

    const res = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: {
        'X-Api-Key': appKeyId,
        'X-Timestamp': ts,
        'X-Nonce': nonce,
        Authorization: signature,
        'Content-Type': 'application/json',
      },
      body,
    });

    const text = await res.text();
    let json;
    try {
      json = JSON.parse(text);
    } catch {
      json = { ret_code: -999, ret_msg: 'non-json response', _raw: text.slice(0, 300) };
    }

    const rateLimited = res.status === 429 || json.ret_code === -11;
    if (rateLimited && attempt < retries) {
      const fromHeader = Number(res.headers.get('retry-after') ?? 0) * 1000;
      const m = /retry after (\d+)/.exec(json.error?.message ?? '');
      const wait = fromHeader || (m ? Number(m[1]) * 1000 : 2 ** attempt * 3000);
      console.error(`  ⏳ 限流，等待 ${Math.round(wait / 1000)}s 后重试 (${attempt + 1}/${retries})`);
      await new Promise((r) => setTimeout(r, wait + 1000));
      continue;
    }
    return { httpStatus: res.status, ...json };
  }
}

/** 修改自选股。op: ADD=加入分组 / MOVE_OUT=仅从该分组移出 / DEL=从所有分组彻底删除 */
export function modifyUserSecurity(op, codeList, groupName) {
  const body = { op, code_list: codeList };
  if (groupName) body.group_name = groupName;
  return apiPost('/api/v1.0/quote/modify-user-security', body);
}

// ---------------------------------------------------------------- CLI
if (process.argv[1] && import.meta.url === `file://${process.argv[1]}`) {
  const [cmd, ...args] = process.argv.slice(2);
  const offset = await syncTime();
  console.error(`[时钟偏移 ${offset}ms]`);

  if (cmd === 'selftest') {
    const r = await apiGet('/api/v1.0/quote/US.FUTU/history-kline', { end: '2026-10-03', ktype: '2', num: '2' });
    console.log('ret_code:', r.ret_code, '| ret_msg:', r.ret_msg, '| http:', r.httpStatus);
    console.log(JSON.stringify(r.data ?? r, null, 2).slice(0, 800));
  } else if (cmd === 'get') {
    const [path, ...kv] = args;
    const params = Object.fromEntries(kv.map((s) => s.split('=')));
    const r = await apiGet(path, params);
    console.log(JSON.stringify(r, null, 2).slice(0, 1500));
  } else if (cmd === 'kline') {
    const [symbol, end] = args;
    const r = await historyKline(symbol, { end });
    console.log('ret_code:', r.ret_code, '| 条数:', r.data?.kline_list?.length ?? 0, '| next_time:', r.data?.next_time);
  } else if (cmd === 'post') {
    const [path, json] = args;
    const r = await apiPost(path, JSON.parse(json));
    console.log(JSON.stringify(r, null, 2).slice(0, 800));
  } else {
    console.log('用法: selftest | get <path> [k=v ...] | post <path> <json> | kline <symbol> <end>');
  }
}
