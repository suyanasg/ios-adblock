#!/usr/bin/env node
/**
 * test-splash.js — 验证 splash.js 的"路径命中放宽键集"策略
 * 运行: node test/test-splash.js
 *
 * 核心断言：同一个 body，走开屏 URL 要被清，走普通 URL 必须不动。
 * 这条守住就不会误伤主接口。
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'dist', 'splash.js'), 'utf8');
const SPLASH_URL = 'https://u.y.qq.com/cgi-bin/musicu.fcg?splash=1';
const NORMAL_URL = 'https://u.y.qq.com/cgi-bin/musicu.fcg?cmd=songlist';

function run(url, body) {
  let out = null;
  const sandbox = {
    $request: { url, method: 'POST', headers: {} },
    $response: { body, headers: {}, status: 200 },
    $done: (r) => { out = r; },
    console: { log: () => {} },
    setTimeout: (fn) => fn(),   // 延时逻辑同步化，方便断言
    JSON, Object, Array, String, Number, Math, Error
  };
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox);
  return out;
}

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  ok   ' + name); }
  else { fail++; console.log('  FAIL ' + name + (extra !== undefined ? '  -> ' + extra : '')); }
}
function body(r) {
  return r && r.body ? JSON.parse(r.body) : null;
}

// ---------- 1. 开屏响应里的 splash 容器 ----------
const splashResp = {
  code: 0,
  data: {
    splash: { ad_id: 77, image: 'https://x/s.jpg', jumpUrl: 'https://x/landing' },
    module: { name: 'home' }
  }
};
let r = run(SPLASH_URL, JSON.stringify(splashResp));
let p = body(r);
check('开屏: splash 容器被删', p && p.data.splash === undefined, p && JSON.stringify(p.data));
check('开屏: 同层业务数据保留', p && p.data.module && p.data.module.name === 'home');

// ---------- 2. 同一 body 走普通路径必须不动 ----------
r = run(NORMAL_URL, JSON.stringify(splashResp));
check('普通路径: 不动 splash 字段', r && Object.keys(r).length === 0, JSON.stringify(r));

// ---------- 3. 开屏语境下的图片+跳转启发式 ----------
const listResp = {
  data: {
    list: [
      { image: 'https://x/a.jpg', jumpUrl: 'https://x/landing' },
      { title: '正常歌曲列表' }
    ]
  }
};
r = run(SPLASH_URL, JSON.stringify(listResp));
p = body(r);
check('开屏: 图片+跳转条目被删', p && p.data.list.length === 1, p && p.data.list.length);
check('开屏: 保留正常条目', p && p.data.list[0].title === '正常歌曲列表');

// ---------- 4. 同样结构走普通路径，零改动 ----------
r = run(NORMAL_URL, JSON.stringify(listResp));
check('普通路径: 图片+跳转不误伤', r && Object.keys(r).length === 0, JSON.stringify(r));

// ---------- 5. 容器键清空：开屏容器应为 []（= 无广告），不是回滚 ----------
const allAd = { data: { splashList: [{ image: 'a', jumpUrl: 'b' }, { image: 'c', jumpUrl: 'd' }], code: 0 } };
r = run(SPLASH_URL, JSON.stringify(allAd));
p = body(r);
check('容器: splashList 清成空数组', p && Array.isArray(p.data.splashList) && p.data.splashList.length === 0,
      p && JSON.stringify(p.data.splashList));
check('容器: 同层标量保留', p && p.data.code === 0);

// ---------- 6. 开屏专用容器键：仅开屏路径生效 ----------
const launch = { result: { launchAd: { id: 1, img: 'x' }, songs: [1, 2, 3] } };
r = run(SPLASH_URL, JSON.stringify(launch));
p = body(r);
check('开屏: launchAd 被删', p && p.result.launchAd === undefined);
check('开屏: launchAd 同层数据保留', p && p.result.songs.length === 3);

r = run(NORMAL_URL, JSON.stringify(launch));
check('普通路径: launchAd 保留', r && Object.keys(r).length === 0, JSON.stringify(r));

// ---------- 7. 非法 JSON / 干净响应 ----------
r = run(SPLASH_URL, '{"code":0,"data":');
check('非法JSON: 放行', r && Object.keys(r).length === 0);

r = run(SPLASH_URL, JSON.stringify({ code: 200, data: { songs: [{ id: 1 }] } }));
check('干净响应: 不重写', r && Object.keys(r).length === 0);

// ---------- 8. 超 3MB ----------
r = run(SPLASH_URL, JSON.stringify({ data: { blob: 'x'.repeat(4 * 1024 * 1024), splash: { a: 1 } } }));
check('超 3MB: 跳过', r && Object.keys(r).length === 0);

console.log('');
console.log(fail === 0 ? `全部通过 ${pass}/${pass}` : `失败 ${fail} 项，通过 ${pass} 项`);
process.exit(fail === 0 ? 0 : 1);
