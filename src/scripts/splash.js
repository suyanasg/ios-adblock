/* ============================================================
 * splash.js  —  开屏 / 启动图广告清洗
 * ------------------------------------------------------------
 * 为什么单独写一份：
 *   开屏接口和主接口同域，域名规则杀不掉；
 *   开屏响应的广告字段名（splash / launchAd / openScreen / promo…）
 *   又远比信息流的花，所以这里用"路径命中即放宽键集"的策略：
 *     - 命中 splash 类路径  -> 额外清 20+ 个开屏专用容器键
 *     - 普通路径            -> 只清基础广告键，误伤极小
 *
 * 安全阀（和 adclean 一致）：
 *   异常原样放行 / 数组清空回滚 / 清洗后无变化不重写 / 超 3MB 跳过
 * ============================================================ */

var MAX_SIZE = 3 * 1024 * 1024;
var DEBUG = false;

/* 开屏路径特征。命中就放宽清洗力度 */
var SPLASH_HINT = /(splash|start[-_]?screen|open[-_]?screen|launch[-_]?ad|boot[-_]?ad|welcome[-_]?ad|openscreen|advert|promo)/i;

/* 可选：延迟放行(毫秒)。让请求拖到 App 侧超时，开屏页会被直接跳过。
 * 默认 0 = 关闭。真机上开了卡在启动图就调回 0。
 * 建议值 2000~3000，别超过模块 timeout(15s)。 */
var DELAY_MS = 0;

/* 基础广告标记（同 adclean） */
var AD_FLAG_KEYS = [
  'is_ad', 'isAd', 'is_ads', 'isAds',
  'is_advert', 'isAdvert', 'is_advertisement',
  'is_promotion', 'isPromotion',
  'is_promoted', 'isPromoted',
  'is_ad_card', 'isAdCard',
  'ad_flag', 'adFlag',
  'is_sponsor', 'isSponsor',
  'show_ad', 'showAd'
];

/* 基础容器键 */
var BASE_CONTAINER_KEYS = [
  'ad', 'ads', 'ad_list', 'ads_list',
  'adInfo', 'ad_info', 'adsInfo', 'ads_info',
  'advert', 'advertisement', 'advertisement_info',
  'promotion', 'promotion_info', 'promotionInfo',
  'sponsor', 'sponsor_info', 'sponsorInfo'
];

/* 开屏专用容器键：只有在命中 SPLASH_HINT 的响应里才生效 */
var SPLASH_CONTAINER_KEYS = [
  'splash', 'splashAd', 'splash_ad', 'splashInfo', 'splash_info',
  'splashList', 'splash_list', 'splashInfoV2',
  'openScreen', 'open_screen', 'openScreenAd', 'open_screen_ad',
  'startScreen', 'start_screen', 'startScreenAd', 'start_screen_ad',
  'startupAd', 'startup_ad', 'startup', 'startupInfo',
  'launchAd', 'launch_ad', 'launchInfo', 'launch_info',
  'bootAd', 'boot_ad', 'bootInfo',
  'welcomeAd', 'welcome_ad', 'welcomeInfo',
  'adResource', 'ad_resource', 'adMaterial', 'ad_material',
  'adCreative', 'ad_creative', 'adCreativeList',
  'promoAd', 'promo_ad', 'promoList', 'promo_list',
  'marketingInfo', 'marketing_info', 'marketInfo', 'market_info',
  'operationAd', 'operation_ad', 'operationInfo', 'operation_info'
];

var AD_TYPE_VALUES = [
  'ad', 'ads', 'advert', 'advertisement',
  'promotion', 'promoted', 'sponsor', 'ad_card', 'adcard'
];

var TYPE_KEYS = ['type', 'card_type', 'cardType', 'goto', 'card_goto', 'item_type', 'itemType'];

var NEST_KEYS = ['mblog', 'target', 'card', 'item', 'feed', 'content', 'data', 'object', 'detail'];

var stats = { removed: 0, trimmed: 0, guarded: 0, changed: false, splash: false };

function isObj(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

function truthyFlag(v) {
  return v === true || v === 1 || v === '1' || v === 'true';
}

function directAdMark(o) {
  var i, k;
  for (i = 0; i < AD_FLAG_KEYS.length; i++) {
    k = AD_FLAG_KEYS[i];
    if (Object.prototype.hasOwnProperty.call(o, k) && truthyFlag(o[k])) return true;
  }
  for (i = 0; i < TYPE_KEYS.length; i++) {
    k = TYPE_KEYS[i];
    if (typeof o[k] === 'string' && AD_TYPE_VALUES.indexOf(o[k].toLowerCase()) >= 0) return true;
  }
  if (typeof o.mblogtypename === 'string' && o.mblogtypename.indexOf('广告') >= 0) return true;
  var at = o.ad_type !== undefined ? o.ad_type : o.adType;
  if (typeof at === 'number' && at > 0 &&
      (o.creative !== undefined || o.ad_id !== undefined || o.adId !== undefined)) return true;
  if (isObj(o.ad_info) && Object.keys(o.ad_info).length > 0) return true;
  if (isObj(o.ad) && Object.keys(o.ad).length > 0) return true;

  // 开屏语境下额外判定：带跳转链接 + 图片 + 曝光字段，基本就是张广告图
  if (stats.splash) {
    var hasPic = o.image !== undefined || o.img !== undefined || o.imageUrl !== undefined ||
                 o.pic !== undefined || o.picUrl !== undefined;
    var hasJump = o.jumpUrl !== undefined || o.jump_url !== undefined || o.link !== undefined ||
                  o.targetUrl !== undefined || o.scheme !== undefined;
    if (hasPic && hasJump) return true;
    if (o.duration !== undefined && o.start_time !== undefined && hasPic) return true;
  }
  return false;
}

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

function containerKeys() {
  if (!stats.splash) return BASE_CONTAINER_KEYS;
  return BASE_CONTAINER_KEYS.concat(SPLASH_CONTAINER_KEYS);
}

function scrubArray(arr, keys, depth) {
  if (depth > 12) return arr;
  var kept = [];
  for (var i = 0; i < arr.length; i++) {
    var item = arr[i];
    if (isObj(item) && isAdObject(item)) {
      stats.removed++;
      stats.changed = true;
      continue;
    }
    kept.push(scrubValue(item, keys, depth + 1));
  }
  if (kept.length === 0 && arr.length > 0) {
    stats.guarded++;
    return arr;
  }
  return kept;
}

function scrubObject(o, keys, depth) {
  if (depth > 12) return o;

  for (var i = 0; i < keys.length; i++) {
    var k = keys[i];
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

  for (var key in o) {
    if (!Object.prototype.hasOwnProperty.call(o, key)) continue;
    var val = o[key];
    if (Array.isArray(val) || isObj(val)) {
      o[key] = scrubValue(val, keys, depth + 1);
    }
  }
  return o;
}

function scrubValue(v, keys, depth) {
  if (Array.isArray(v)) return scrubArray(v, keys, depth);
  if (isObj(v)) return scrubObject(v, keys, depth);
  return v;
}

function finish(out) {
  if (DELAY_MS > 0 && stats.changed && stats.splash) {
    // 拖到 App 侧超时，开屏页被直接跳过
    setTimeout(function () { $done({ body: out }); }, DELAY_MS);
    return;
  }
  $done({ body: out });
}

function main() {
  var url = ($request && $request.url) || '';
  var body = $response && $response.body;

  if (SPLASH_HINT.test(url)) stats.splash = true;

  if (typeof body !== 'string' || body.length < 2 || body.length > MAX_SIZE) {
    return $done({});
  }
  var head = body.charAt(0);
  if (head !== '{' && head !== '[') {
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

  var keys = containerKeys();
  try {
    scrubValue(root, keys, 0);
  } catch (e) {
    return $done({});
  }

  if (!stats.changed) {
    if (DEBUG) console.log('[splash] no-ad ' + url);
    return $done({});
  }

  var out;
  try {
    out = JSON.stringify(root);
  } catch (e) {
    return $done({});
  }
  if (out === body) return $done({});

  if (DEBUG) {
    console.log('[splash] splash=' + stats.splash + ' removed=' + stats.removed +
                ' trimmed=' + stats.trimmed + ' url=' + url);
  }
  finish(out);
}

try {
  main();
} catch (e) {
  $done({});
}
