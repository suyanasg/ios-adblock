#!/usr/bin/env node
/**
 * test.js — 用真实结构的样例 JSON 验证 adclean.js 的清洗行为
 * 运行: node test/test.js
 * 覆盖：广告对象删除 / 全广告数组回滚 / 非法 JSON 放行 / 容器字段清空 / 非广告内容零改动
 */
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'dist', 'adclean.js'), 'utf8');

function run(url, body) {
  let out = null;
  const sandbox = {
    $request: { url, method: 'GET', headers: {} },
    $response: { body, headers: { 'Content-Type': 'application/json' }, status: 200 },
    $done: (r) => { out = r; },
    console: { log: () => {} },
    JSON, Object, Array, String, Number, Math, Error
  };
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox);
  return out;
}

let pass = 0, fail = 0;
function check(name, cond, extra) {
  if (cond) { pass++; console.log('  ok   ' + name); }
  else { fail++; console.log('  FAIL ' + name + (extra ? '  -> ' + extra : '')); }
}

// ---------- 1. B 站推荐流：3 条里 1 条广告 ----------
const bili = {
  code: 0,
  data: {
    items: [
      { id: 1, card_type: 'small_cover_v2', title: '正常视频' },
      { id: 2, card_goto: 'ad', title: '推广卡片', ad_info: { ad_id: 99 } },
      { id: 3, card_type: 'small_cover_v2', title: '正常视频2' }
    ]
  }
};
let r = run('https://app.bilibili.com/x/v2/feed/index', JSON.stringify(bili));
let parsed = r && r.body ? JSON.parse(r.body) : null;
check('B站: 删掉 1 条广告卡片', parsed && parsed.data.items.length === 2, parsed && parsed.data.items.length);
check('B站: 保留正常条目', parsed && parsed.data.items[0].title === '正常视频');

// ---------- 2. 全是广告的数组必须回滚 ----------
const allAd = {
  data: {
    list: [
      { is_ad: true, id: 1 },
      { is_ad: 1, id: 2 }
    ]
  }
};
r = run('https://api.example.com/x', JSON.stringify(allAd));
check('回滚: 全广告数组不返回空', r && Object.keys(r).length === 0, JSON.stringify(r));
const rolled = r && r.body ? JSON.parse(r.body) : null;
check('回滚: 原数组完整保留', !rolled || rolled.data.list.length === 2);

// ---------- 3. 非法 JSON 必须原样放行 ----------
r = run('https://api.example.com/x', '{ this is not json ');
check('非法JSON: 不改写', r && Object.keys(r).length === 0, JSON.stringify(r));

// ---------- 4. 微博广告标记 mblogtypename ----------
const weibo = {
  ok: 1,
  data: {
    cards: [
      { card_type: 9, mblog: { id: 'a1', text: '普通微博' } },
      { card_type: 9, mblog: { id: 'a2', text: '广告微博', mblogtypename: '广告' } }
    ]
  }
};
r = run('https://api.weibo.cn/2/statuses/unread_hot_timeline', JSON.stringify(weibo));
parsed = r && r.body ? JSON.parse(r.body) : null;
check('微博: 广告微博被过滤', parsed && parsed.data.cards.length === 1, parsed && parsed.data.cards.length);

// ---------- 5. 容器字段 ad / ads 清空 ----------
const splashed = {
  code: 0,
  data: {
    splash: { ad: { id: 1, img: 'http://x/a.jpg' }, ads: [{ id: 2 }], title: '首页' }
  }
};
r = run('https://www.xiaohongshu.com/api/sns/v10/splash', JSON.stringify(splashed));
parsed = r && r.body ? JSON.parse(r.body) : null;
check('容器: ad 对象被删除', parsed && parsed.data.splash.ad === undefined);
check('容器: ads 数组被清空', parsed && Array.isArray(parsed.data.splash.ads) && parsed.data.splash.ads.length === 0);
check('容器: 同层正常字段保留', parsed && parsed.data.splash.title === '首页');

// ---------- 6. 干净响应零改动 ----------
const clean = { code: 0, data: { list: [{ id: 1, title: '正常' }, { id: 2, title: '正常2' }] } };
r = run('https://api.example.com/x', JSON.stringify(clean));
check('干净响应: 不重写 body', r && Object.keys(r).length === 0);

// ---------- 7. 超大 body 跳过 ----------
const huge = { data: { blob: 'x'.repeat(4 * 1024 * 1024), ad: { a: 1 } } };
r = run('https://api.example.com/x', JSON.stringify(huge));
check('超过 3MB: 直接跳过', r && Object.keys(r).length === 0);

// ---------- 8. 非 JSON（HTML/图片）跳过 ----------
r = run('https://api.example.com/x', '<html><body>hi</body></html>');
check('HTML: 不改写', r && Object.keys(r).length === 0);

// ---------- 9. 角标文案兜底：字段名猜不到，但角标写着「广告」 ----------
const badge = {
  data: {
    list: [
      { id: 1, title: '正常内容', label: '热榜' },
      { id: 2, title: '某品牌新品', label: '广告' },
      { id: 3, title: '唇釉新品', tag: '广告·天猫' },
      { id: 4, title: '广告设计大赛征稿' },
      { id: 5, title: '推广 | 好物' }
    ]
  }
};
r = run('https://api.example.com/x', JSON.stringify(badge));
parsed = r && r.body ? JSON.parse(r.body) : null;
check('角标: label="广告" 被删', parsed && !parsed.data.list.some(x => x.id === 2));
check('角标: tag="广告·天猫" 被删', parsed && !parsed.data.list.some(x => x.id === 3));
check('角标: 误杀检查 title="广告设计大赛征稿" 保留', parsed && parsed.data.list.some(x => x.id === 4));
check('角标: 正常标签 "热榜" 保留', parsed && parsed.data.list.some(x => x.id === 1));

// ---------- 10. 角标词表不能吃正常内容 ----------
const notAd = {
  data: {
    list: [
      { id: 1, title: 'A', label: '广告位招租' },
      { id: 2, title: 'B', tag: '热搜' }
    ]
  }
};
r = run('https://api.example.com/x', JSON.stringify(notAd));
check('角标: "广告位招租"(非纯角标) 不触发', r && Object.keys(r).length === 0, JSON.stringify(r));

console.log('');
console.log(fail === 0 ? `全部通过 ${pass}/${pass}` : `失败 ${fail} 项，通过 ${pass} 项`);
process.exit(fail === 0 ? 0 : 1);
