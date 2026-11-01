/* ============================================================
 * adclean.js  —  Shadowrocket / Surge 通用 JSON 广告清洗引擎
 * ------------------------------------------------------------
 * 设计原则：
 *   1. 任何异常都必须原样放行（$done({})），绝不把 App 弄崩
 *   2. 只删「明确标了广告标记」的对象，不做模糊文本匹配
 *   3. 数组被清空时回滚，避免推荐流空白
 *   4. 非 JSON / 超大 body 直接跳过
 * ============================================================ */

var MAX_SIZE = 3 * 1024 * 1024; // 3MB 以上不处理
var DEBUG = false;              // 需要看日志时改 true，会 console.log 计数

/* 布尔型广告标记：值为 true / 1 / "1" 时，整个对象视为广告 */
var AD_FLAG_KEYS = [
  'is_ad', 'isAd', 'is_ads', 'isAds',
  'is_advert', 'isAdvert', 'is_advertisement',
  'is_promotion', 'isPromotion',
  'is_promoted', 'isPromoted',
  'is_ad_card', 'isAdCard',
  'ad_flag', 'adFlag',
  'is_sponsor', 'isSponsor'
];

/* 容器型广告字段：值为数组时清空，为对象时删除该键 */
var AD_CONTAINER_KEYS = [
  'ad', 'ads', 'ad_list', 'ads_list',
  'adInfo', 'ad_info', 'adsInfo', 'ads_info',
  'ad_info_v2', 'adInfoV2',
  'advert', 'advertisement', 'advertisement_info',
  'promotion', 'promotion_info', 'promotionInfo',
  'sponsor', 'sponsor_info', 'sponsorInfo',
  'adData', 'ad_data'
];

/* 类型字段等于这些字符串时视为广告卡片 */
var AD_TYPE_VALUES = [
  'ad', 'ads', 'advert', 'advertisement',
  'promotion', 'promoted', 'sponsor', 'ad_card', 'adcard'
];

/* 类型字段名 */
var TYPE_KEYS = ['type', 'card_type', 'cardType', 'goto', 'card_goto', 'item_type', 'itemType'];

/* 角标文案字段名：字段值恰好等于下面词表里的短词才算广告。
 * 用判等不用包含，避免把「广告设计大赛」这种正常标题干掉。 */
var BADGE_KEYS = [
  'label', 'badge', 'tag', 'mark', 'corner',
  'corner_text', 'cornerText', 'card_tag', 'cardTag',
  'mark_text', 'markText', 'ad_label', 'adLabel',
  'type_name', 'typeName', 'tag_name', 'tagName'
];

/* 严格白名单。带「·」的组合如「广告·天猫」走前缀判断 */
var BADGE_WORDS = [
  '广告', '推广', '赞助', '商业推广', '品牌广告', '赞助内容', '推广内容',
  'AD', 'Sponsored', 'Promoted', 'Ad'
];

// 微博：mblogtypename 是博文类型名，值通常就是「广告」
function badgeHit(v) {
  if (typeof v !== 'string') return false;
  var s = v.trim();
  if (!s || s.length > 12) return false;
  for (var i = 0; i < BADGE_WORDS.length; i++) {
    if (s === BADGE_WORDS[i]) return true;
  }
  // 「广告·天猫」「推广 | 某品牌」这种：前缀后续必须跟分隔符，
  // 否则「广告设计大赛」会被误杀
  if (s.indexOf('广告') === 0 || s.indexOf('推广') === 0 || s.indexOf('赞助') === 0) {
    var rest = s.slice(2);
    if (rest && '·|｜-—:：、、 '.indexOf(rest.charAt(0)) >= 0) return true;
  }
  return false;
}


/* 广告标记可能藏在子对象里（微博 mblog、知乎 target、B站 card…） */
var NEST_KEYS = ['mblog', 'target', 'card', 'item', 'feed', 'content', 'data', 'object', 'detail'];

var stats = { removed: 0, trimmed: 0, guarded: 0, changed: false };

function isObj(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

function truthyFlag(v) {
  return v === true || v === 1 || v === '1' || v === 'true';
}

/* 只看当前层级的广告标记 */
function directAdMark(o) {
  var i, k;
  for (i = 0; i < AD_FLAG_KEYS.length; i++) {
    k = AD_FLAG_KEYS[i];
    if (Object.prototype.hasOwnProperty.call(o, k) && truthyFlag(o[k])) return true;
  }
  for (i = 0; i < TYPE_KEYS.length; i++) {
    k = TYPE_KEYS[i];
    if (typeof o[k] === 'string' && AD_TYPE_VALUES.indexOf(o[k].toLowerCase()) >= 0) {
      return true;
    }
  }
  // 微博：mblogtypename 是博文类型名，值通常就是「广告」
  if (badgeHit(o.mblogtypename)) return true;
  if (typeof o.mblog_type === 'string' && o.mblog_type.indexOf('ad') === 0) return true;
  // 角标文案兜底：字段名猜不到，但角标写着「广告」就是广告
  for (i = 0; i < BADGE_KEYS.length; i++) {
    k = BADGE_KEYS[i];
    if (typeof o[k] === 'string' && badgeHit(o[k])) return true;
  }
  // 数值型 ad_type > 0 且带创意/素材字段，基本可判定为广告
  var at = o.ad_type !== undefined ? o.ad_type : o.adType;
  if (typeof at === 'number' && at > 0 &&
      (o.creative !== undefined || o.ad_id !== undefined || o.adId !== undefined)) {
    return true;
  }
  // 直接挂着非空 ad / ad_info 的对象
  if (isObj(o.ad_info) && Object.keys(o.ad_info).length > 0) return true;
  if (isObj(o.ad) && Object.keys(o.ad).length > 0) return true;
  return false;
}

/* 向下探 2 层，覆盖"卡片包微博""feed 包 target"这类结构 */
function isAdObject(o, depth) {
  if (!isObj(o)) return false;
  if (depth === undefined) depth = 0;
  if (directAdMark(o)) return true;
  if (depth >= 2) return false;
  for (var i = 0; i < NEST_KEYS.length; i++) {
    var k = NEST_KEYS[i];
    if (isObj(o[k]) && isAdObject(o[k], depth + 1)) return true;
  }
  return false;
}

function scrubArray(arr, depth) {
  if (depth > 12) return arr;
  var kept = [];
  for (var i = 0; i < arr.length; i++) {
    var item = arr[i];
    if (isObj(item) && isAdObject(item)) {
      stats.removed++;
      stats.changed = true;
      continue;
    }
    kept.push(scrubValue(item, depth + 1));
  }
  // 全被删光 -> 回滚，避免页面空白
  if (kept.length === 0 && arr.length > 0) {
    stats.guarded++;
    return arr;
  }
  return kept;
}

function scrubObject(o, depth) {
  if (depth > 12) return o;

  // 1) 先清广告容器字段
  for (var i = 0; i < AD_CONTAINER_KEYS.length; i++) {
    var k = AD_CONTAINER_KEYS[i];
    if (!Object.prototype.hasOwnProperty.call(o, k)) continue;
    var v = o[k];
    if (Array.isArray(v)) {
      if (v.length > 0) {
        stats.trimmed += v.length;
        stats.changed = true;
        o[k] = [];
      }
    } else if (isObj(v)) {
      if (Object.keys(v).length > 0) {
        stats.trimmed++;
        stats.changed = true;
        delete o[k];
      }
    }
  }

  // 2) 再递归处理剩余字段
  for (var key in o) {
    if (!Object.prototype.hasOwnProperty.call(o, key)) continue;
    var val = o[key];
    if (Array.isArray(val) || isObj(val)) {
      o[key] = scrubValue(val, depth + 1);
    }
  }
  return o;
}

function scrubValue(v, depth) {
  if (Array.isArray(v)) return scrubArray(v, depth);
  if (isObj(v)) return scrubObject(v, depth);
  return v;
}

function main() {
  var body = $response && $response.body;
  if (typeof body !== 'string' || body.length < 2 || body.length > MAX_SIZE) {
    return $done({});
  }
  var head = body.charAt(0);
  if (head !== '{' && head !== '[') {
    // 有些接口在 JSON 前面塞了防劫持前缀，尝试定位
    var p = body.indexOf('{');
    if (p < 0 || p > 8) return $done({});
    body = body.slice(p);
  }

  var root;
  try {
    root = JSON.parse(body);
  } catch (e) {
    return $done({});
  }

  try {
    scrubValue(root, 0);
  } catch (e) {
    return $done({});
  }

  if (!stats.changed) {
    if (DEBUG) console.log('[adclean] no-ad ' + ($request ? $request.url : ''));
    return $done({});
  }

  var out;
  try {
    out = JSON.stringify(root);
  } catch (e) {
    return $done({});
  }

  // 清洗后和原文一样（比如全广告数组被回滚）就没必要改写，省一次内存拷贝
  if (out === body) {
    if (DEBUG) console.log('[adclean] unchanged-after-scrub ' + ($request ? $request.url : ''));
    return $done({});
  }

  if (DEBUG) {
    console.log('[adclean] removed=' + stats.removed + ' trimmed=' + stats.trimmed +
                ' guarded=' + stats.guarded + ' url=' + ($request ? $request.url : ''));
  }
  $done({ body: out });
}

try {
  main();
} catch (e) {
  $done({});
}
