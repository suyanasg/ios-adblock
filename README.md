# iOS 小火箭去广告模块

给 Shadowrocket 用的去广告配置，分三档强度，全部可回滚。

```
ios-adblock/
├── build.py              构建脚本（校验 + 去重 + 剪冗余子域 + 出产物）
├── sync.py               从 5 个上游抓广告域名，三道闸门过滤后写 auto.list
├── serve.py              本机起 HTTP，把模块直接喂给手机
├── deploy-gist.py        把脚本和模块推到 Gist，手机上订阅 raw 地址
├── daily-build.ps1       本机每日构建（可 -Install 注册计划任务）
├── .github/workflows/daily.yml   GitHub Actions 每日构建
├── src/rules/core.list   手写安全区（165 条）
├── src/rules/auto.list   机器抓的 A 档（约 2000 条，sync.py 生成）
├── src/rules/auto-extra.list   机器抓的 B 档（默认不启用）
├── src/rules/aggressive.list  激进区（默认不启用）
├── src/rules/risk.list   风险区（默认不启用，杀推送/登录/风控）
├── src/rules/guard.list  护栏（15 条 DIRECT，先于外链生效）
├── src/rules/rewrite.list     开屏接口 reject
├── src/rules/mitm.txt    信息流脚本需要解密的域名
├── src/rules/mitm-splash.txt  开屏脚本需要解密的域名（音乐 App）
├── src/scripts/adclean.js     JSON 信息流广告清洗
├── src/scripts/splash.js      开屏广告清洗（音乐 App 专用键集）
├── test/test.js          信息流清洗单测（17 项）
├── test/test-splash.js   开屏清洗单测（14 项）
└── dist/                 产物
```

## 四档强度

| 档位 | 产物 | 需要证书 | 风险 | 效果 |
|---|---|---|---|---|
| 纯规则 | `adblock-lite.module` | 不需要 | 极低 | 约 2400 条纯域名拦截（165 手写 + 2000 自动），Web 广告基本清空，App 内嵌广告保留 |
| 完整 | `adblock-pro.module` | 需要 | 中 | 再加开屏接口 reject + JSON 广告清洗，B站/微博/知乎/小红书/贴吧信息流广告消失，音乐 App 开屏一并清掉 |
| 外链 | `adblock-pro-plus.module` | 需要 | 中 | 完整版 + `DOMAIN-SET` 挂 anti-AD 全量域名集（6.8 万条），规则随小火箭自动更新，已内置护栏 |
| 风险 | `--with-risk` | 视情况 | 高 | 追加推送 SDK / 风控 / 第三方登录 / 深度链接域名，覆盖更全但会**打断 App 功能** |
| 激进 | `--with-aggressive` | 视情况 | 很高 | 按关键词杀域名，会误伤图片 CDN 和登录接口 |

**先上纯规则版跑两天**，没问题再考虑完整版。

## 风险区：哪些域名故意没拦

`src/rules/risk.list` 里那批默认**不编进模块**。它们确实在跑广告或统计，但同时承担别的功能，杀了会出这些症状：

| 域名 | 干什么的 | 杀了之后 |
|---|---|---|
| `getui.com` / `igexin.com` | 个推推送通道 | App 收不到通知 |
| `jpush.cn` / `jiguang.cn` | 极光推送 | 同上 |
| `mob.com` | Mob ShareSDK | 微信 / QQ 第三方登录失败 |
| `tongdun.cn` / `fraudmetrix.cn` | 同盾设备指纹风控 | 登录、下单、支付提示"网络异常" |
| `branch.io` | 深度链接 | 分享链接、邀请码、免安装跳转打不开 |
| `unity.com` | Unity 官网 / Hub / 资源商店 | 官网和编辑器授权全废 |

要更狠就 `python build.py --with-risk`，但建议一次只观察两三天。**广告没清干净是小事，App 用不了是大事。**

早期版本把 `unity.com` 直接编进核心区，这是明确的误伤——真正的 Unity 广告 SDK 是 `unityads.unity3d.com`，已经单独保留。`risk.list` 里也删掉了，任何档位都不该杀官网。

## 护栏：plus 版为什么没把推送杀掉

`--with-risk` 那批域名你觉得危险，就没编进去。但 plus 版挂的 anti-AD 全量集**不管你的分级**——它里面本来就有 `getui.com`、`jpush.cn`、`mob.com`、`branch.io`。

实测：这几个推送/登录域名在 anti-AD 里有 **73 条子域**被标成广告，加上证书、验证码、小程序那几条，一共 **83 条**。不处理的话，你导入 plus 的那一刻起，推送就静默了，第三方登录开始转圈。

所以 plus 版在 `[Rule]` 最前面插了一组 `guard.list`：

```
[Rule]
# --- guard: must stay above every REJECT line below ---
DOMAIN,crl.microsoft.com,DIRECT
DOMAIN-SUFFIX,getui.com,DIRECT
...
DOMAIN-SUFFIX,ads.y.qq.com,REJECT      ← 精选开始
...
DOMAIN-SET,https://anti-ad.net/domains.txt,REJECT   ← 外链最后
```

小火箭规则**自上而下匹配，首个命中生效**。护栏排在最前，外链的阴影就盖不到它。

护栏一共 15 条，覆盖这些：

| 类别 | 域名 | 断了会怎样 |
|---|---|---|
| 证书 | `crl.microsoft.com` | 证书吊销校验超时，HTTPS 连接卡几秒 |
| 登录 | `cmpassport.com`、`cm.passport.iqiyi.com`、`sts.microsoft.com`、`mob.com` | 移动一键登录、爱奇艺登录、Office 365 登录失败 |
| 验证码 | `fp-upload.dun.163.com`、`ye.dun.163yun.com` | 卡在网易易盾验证码页，报"网络异常" |
| 推送 | `getui.com`、`getui.net`、`igexin.com`、`jpush.cn`、`jiguang.cn` | App 彻底静默 |
| 深度链接 | `branch.io` | 分享链接、邀请码打不开 |
| 其他 | `miniapp.bilibili.com`、`securemetrics.apple.com` | B 站小程序白屏；Apple 交易指标上报异常 |

**统计、归因、崩溃上报一律不放行。** 那是去广告该杀的，别混进护栏。

不想要护栏（比如你根本不用国内推送）：

```bash
python build.py --no-guard
```

## 体检：外链到底挂进来什么

```bash
python build.py --audit-upstream
```

不用等手机出问题，构建时就把外链拉下来算一遍：

```
上游域名集体检（要拉几 MB，慢）：
  OK   https://anti-ad.net/domains.txt
       67802 条域名（0 行非域名、0 条重复）

  域名总数      : 67802
  被父域覆盖    : 6174  (9.1%，挂上去纯占内存)
  重复行        : 0
  精选重复      : 80 / 165  (48% 已被外链覆盖)
  精选独有      : 85 条（外链没有，删了就是裸奔）

  护栏命中：
    getui.com                          外链里 13 条，已前置放行
    jpush.cn                           外链里 15 条，已前置放行
    ...

  MITM 域体检：
    18 个 MITM 域全部安全，脚本能正常拿到响应
```

三个指标怎么读：

- **护栏命中全部 >0** —— 说明护栏没白写。如果哪条变成 0，说明上游已经不放它了，可以从 `guard.list` 删掉
- **MITM 域全部安全** —— 如果这里出现 `!!`，说明上游把 `u.y.qq.com` 这类域名杀了。REJECT 在网络层，MITM 在应用层，被杀就永远拿不到响应，plus 的脚本层等于废了。这种情况把那几个域名也加进 `guard.list`
- **精选 48% 重复** —— 这 80 条不是白留。外链拉不到的时候（网络断、anti-AD 挂掉），精选就是兜底。165 条占 0.2% 的内存，换一个"外链失效也不裸奔"的保险，划算

网络差的时候用本地缓存体检，不用重拉：

```bash
curl -o /tmp/aa.txt https://anti-ad.net/domains.txt
python build.py --audit-upstream --audit-source /tmp/aa.txt
```

## 广告识别的四层，从硬到软

`adclean.js` 判断一条内容是不是广告，按这个顺序：

1. **布尔标记** —— `is_ad` / `isPromoted` / `ad_flag` 这类字段为真
2. **类型字段** —— `card_type` / `goto` / `item_type` 等于 `ad` / `promotion` / `sponsor`
3. **容器字段** —— 对象里挂着非空的 `ad` / `ads` / `ad_info` / `adData`
4. **角标文案** —— 字段名猜不到，但 `label` / `tag` / `mark` 的值恰好是「广告」「推广」「赞助」

第 4 层是兜底，规则收得很紧：

- 只认**判等**和**带分隔符的前缀**（`广告·天猫` 命中）
- `广告设计大赛征稿` **不命中**——没有分隔符
- `广告位招租` **不命中**——长度和结构都不对

宁可漏掉一条广告，也不误删一条正常内容。这是这个脚本的底线。

## 快速开始（本机推送）

电脑和手机连同一个 Wi-Fi，然后：

```bash
python serve.py
```

屏幕会直接打出三条地址，复制粘进小火箭：

```
配置 > 模块 > 右上角 + > 填地址 > 下载
```

导完就可以 Ctrl+C 关掉服务，模块内容已经在手机上了。**不要**把 `127.0.0.1` 的地址填进去，手机上的 127.0.0.1 是手机自己。

## 纯规则版（不想开服务就手贴）

```bash
python build.py
```

打开 `dist/adblock-lite.module`，全文复制 → 小火箭 `配置 > 模块 > 新建模块` → 粘贴 → 保存。

纯规则模块不含 MITM，所以在任何全局路由模式下都生效。

## 完整版前置（别跳过）

1. `配置 > 点当前配置 > HTTPS 解密 > 生成新的 CA 证书`
2. 安装描述文件：`设置 > 通用 > VPN 与设备管理`
3. 信任证书：`设置 > 通用 > 关于本机 > 证书信任设置 > 打开完全信任`
4. 小火箭里打开「HTTPS 解密」总开关
5. 全局路由切到**「配置」**——带规则的模块不走配置模式不生效
6. 再导入 `adblock-pro.module`

模块里的 `[MITM]` 用的是 `%APPEND%`，只会往你现有配置里追加域名，不会覆盖别的模块。

## 去开屏广告（音乐 App）

开屏广告比信息流顽固，原因有三个，对不上就别怪模块没生效：

1. **开屏接口和主接口同域。** `u.y.qq.com` 既发歌单也发开屏，域名规则一杀全哑。
2. **图片早缓存到本地了。** 接口拦了，App 还拿缓存图顶上。第一次上模块大概率还会看到一次，等缓存过期（通常 1 天）或者删掉 App 重装才会彻底消失。
3. **广告素材和业务数据在同一个 JSON 里。** 不能整个 reject，只能挑字段删。

所以 `splash.js` 打了个分层：**同一份脚本，路径命中开屏特征才放宽键集**。

| 路径 | 清洗的键 |
|---|---|
| 普通接口 | `ad` / `ads` / `promotion` 等基础键 |
| 命中 `splash\|launch\|startup\|open_screen\|boot_ad\|promo` | 额外 40+ 个开屏专用键（`splashAd` / `launchAd` / `openScreen` / `adMaterial`…），并启用「图片+跳转链接=广告」启发式 |

命中的域名（`mitm-splash.txt`）：QQ音乐 `u.y.qq.com`/`u6.y.qq.com`、网易云 `interface.music.163.com`/`music.163.com`、酷狗 `m.kugou.com`+`ads.kugou.com`、酷我 `m.kuwo.cn`+`ads.kuwo.cn`。

汽水音乐是字节系、接口和抖音共用，默认注释掉了，要用自己去 `mitm-splash.txt` 里放开。

### 三板斧，按顺序试

**第一板：脚本清洗（默认已开）**
导入完整版就有。刷日志看有没有 `[splash] splash=true removed=N`。

**第二板：硬 reject 开屏接口**
`rewrite.list` 底部有一段注释掉的音乐开屏 regex，把对应 App 那行的 `#` 删掉再 `python build.py`。

比脚本彻底——请求直接死掉，App 一份素材都拿不到。代价是部分版本会白屏/转圈 1-3 秒。**一次只开一个 App**，验完再加下一个。

**第三板：延迟放行**
`src/scripts/splash.js` 顶部 `var DELAY_MS = 0;` 改成 `2500`。

让开屏请求拖到 App 侧超时，App 直接跳过开屏页进主页。这是最干净的效果，但**依赖 App 有优雅的超时处理**——开了卡在启动图就立刻调回 0。这板没有真机验证过，属于试出来的参数。

### 一个诚实的提醒

`u.y.qq.com` / `music.163.com` 开着 MITM，意味着小火箭能看这几个域名的全部明文流量，而且这些接口流量不小，会有可感知的耗电和延迟。如果只想清爽网页、不在乎 App 开屏，把 `mitm-splash.txt` 整个注释掉，纯规则版就够用。

## 生效验证

导完后打开 `工具 > 日志`（或 `设置 > 日志级别 = 调试`）：

- 纯规则版：访问带广告的网页，日志里能看到命中规则的域名走 `REJECT`
- 完整版：刷 B 站首页，日志里出现 `adclean removed=N`，N > 0 就是打穿了

## 排错

| 现象 | 原因 | 处理 |
|---|---|---|
| 模块导入了但没反应 | 全局路由不是「配置」 | 切成配置模式 |
| 有规则命中但页面广告还在 | 广告域名走 HTTPS 没解密 | 开 HTTPS 解密 + 信任证书 |
| 开了完整版后 App 转圈/白屏 | MITM 域名开太宽或误删关键字段 | 先关模块确认，再逐条注释 `mitm.txt` 定位 |
| 某网站打不开 | 命中了激进区关键词 | 别开 `--with-aggressive` |
| `adclean` 日志一直 no-ad | App 换了接口字段名 | 看日志里的 URL，往 `AD_FLAG_KEYS` 补标记 |
| 开屏广告还在，日志有 `splash=true` | 本地缓存没过期 | 等一天 / 删 App 重装，别急着改脚本 |
| 开屏广告还在，日志完全没动静 | 域名没进 MITM 或没命中路径关键词 | 看日志里开屏请求的真实 URL，补进 `mitm-splash.txt` |
| 开了延迟放行卡在启动图 | 该 App 没做超时处理 | `DELAY_MS` 调回 0 |
| 音乐 App 白屏 / 转圈 | 用了硬 reject | 把 `rewrite.list` 里对应行重新注释掉 |

## 已知限制（说清楚，别指望它万能）

- **洗的是 JSON 字段，不是视觉元素。** 广告位容器还在，可能出现一块空白占位。
- **App 接口一变就失效。** 这类模块本质是跟着人家 API 跑，属于消耗品，得跟着改。
- **同域广告删不掉。** 广告和正文共用一个接口/域名的（YouTube 贴片、Instagram 赞助帖、抖音信息流）靠域名规则和 JSON 清洗都没戏。
- **证书有代价。** 开了 MITM，小火箭能看你这些域名的明文流量。所以只列了 18 个必要域名（`mitm.txt` 11 个 + `mitm-splash.txt` 9 个），别贪多。
- **不支持 iOS 15 以下。** 低版本 NE 内存只有 15MB，脚本容易把 VPN 撑断。
- **plus 的外链有你用不上的规则。** 6.8 万条里 6174 条（9.1%）是被父域覆盖的冗余子域，还有一堆你已经能忍的国内小站。`DOMAIN-SET` 是远端列表，客户端改不了，只能整条吃。想裁就自建镜像。
- **plus 首次加载慢。** 1.3 MB 的列表，实测这条线只有 10-20 KB/s，等两三分钟是正常的。`pro` 没这个问题。

### 顽固广告分三类，只有第一类能靠这个模块解决

| 类型 | 典型 | 本模块 | 真正解法 |
|---|---|---|---|
| 独立广告域 | 广告 SDK、监测域名、广告 CDN | ✅ 域名规则直接杀 | 已经是最优 |
| 同域但要解析结构 | B站/微博/知乎/小红书信息流、音乐 App 开屏 | ✅ JSON 清洗 | 需要不断跟接口 |
| 同域且加密 | 视频贴片（爱奇艺/优酷/腾讯视频）、抖音信息流、微信朋友圈 | ❌ 打不到 | 得逆向播放协议，改 `vd.l.qq.com` 这类加密响应 |

第三类是真正的天花板，原因不是规则写得不够，是**技术上够不着**：

- 播放接口返回的是加密 JSON，字段名和结构每次版本更新都可能变
- 贴片广告和正片在同一个播放列表里，App 校验总时长，删了广告段会直接播不动
- 抖音/微信这类会把「异常客户端」上报，改接口容易被风控标记

社区里能跳贴片的方案，本质是逆向播放器的解密流程，属于另一个项目量级，而且**每次 App 更新大概率失效**。这个模块不碰。

所以判断标准很简单：**广告有没有一个独立的域名或接口？有就能杀，没有就别指望。**

## 改造 / 维护

改规则：直接编辑 `src/rules/*.list` 后 `python build.py`。构建时会自动：

- 校验域名格式和正则合法性，写错直接失败
- 去重
- 剪掉被上级 `DOMAIN-SUFFIX` 覆盖的子域（少一条就少一点内存）
- 输出 `dist/manifest.txt` 构建报告

改清洗逻辑：

```bash
python build.py && node test/test.js && node test/test-splash.js
```

测试覆盖了广告删除、全广告数组回滚、非法 JSON 放行、超 3MB 跳过、干净响应零改写、角标文案的误杀保护，开屏那份还额外守住「同一 body 走普通路径必须零改动」——**改动后必须 31 项全绿再上机**，不然就是把 App 弄崩。

想扩量，可以再挂社区规则集，在模块 `[Rule]` 段加：

```
RULE-SET,https://raw.githubusercontent.com/deezertidal/shadowrocket-rules/main/rules/adblock.list,REJECT
```

外部规则集挂了不影响本模块自己的 165 条。

## 仓库订阅（推荐）

模块已经推到 <https://github.com/suyanasg/ios-adblock>，raw 地址可以直接订阅：

| 模块 | 订阅地址 |
|---|---|
| 纯规则 | `https://raw.githubusercontent.com/suyanasg/ios-adblock/main/dist/adblock-lite.module` |
| 完整 | `https://raw.githubusercontent.com/suyanasg/ios-adblock/main/dist/adblock-pro.module` |
| 外链 | `https://raw.githubusercontent.com/suyanasg/ios-adblock/main/dist/adblock-pro-plus.module` |

小火箭：`配置 > 模块 > 右上角 + > 填地址 > 下载`。之后这个地址就是订阅，不用再手动重导。

### 本机每天推一次

`daily-build.ps1` 在构建完之后会 `git add -A && commit && push`。
token 从项目根目录的 `.git-token` 读，那个文件已经在 `.gitignore` 里：

```powershell
echo ghp_xxxxxxxx > .git-token
```

classic PAT，**只要 `repo`**。推完 raw 地址立刻可用（GitHub CDN 有 1-2 分钟缓存，不是没生效）。

token 别提交，也别打印到日志里。`.git-token` 被删掉就自动跳过推送，脚本不会报错。

### GitHub Actions 版本

`.github/workflows/daily.yml` 已经写好，但推这个文件需要 token 额外勾 **`workflow`** scope ——
GitHub 不允许只有 `repo` 的 token 创建或修改 workflow。

补了 scope 之后：

```bash
git add .github/workflows/daily.yml
git commit -m "ci: 每日构建"
git push
```

Actions 每天 UTC 20:00（北京 04:00）跑。它的价值是不依赖这台电脑开机；
本机推送则是不依赖 GitHub Actions 额度。两条路结果一样，选一条就行。
**别两个同时开** —— 会互相抢着 commit，白白多出冲突。

## 托管到公网（Gist）

本机 `serve.py` 只是传输工具。要长期用完整版，脚本必须挂公网。



**当前线上地址**（已生效，`dist/adblock-pro.module` 已指向这里）：

```
adclean.js  https://gist.githubusercontent.com/suyanasg/115df6b1947d22f7d356f594f0ce1223/raw/adclean.js
splash.js   https://gist.githubusercontent.com/suyanasg/115df6b1947d22f7d356f594f0ce1223/raw/splash.js
```

### 1. 建 Gist

有 classic PAT（勾 `gist`）的话全自动，一条命令建好并推五个文件：

```bash
set GIST_TOKEN=ghp_xxxxxxxx
python deploy-gist.py --auto
```

它会打印出 Gist 地址和要填进模块的 raw 地址。

> 注意：fine-grained token **不支持** Gist API；设备码授权拿到的 `ghu_` 属于 GitHub App 用户 token，GitHub CLI 那个 OAuth App 没申请 gist 权限，一律 403。只认 classic `ghp_`。

手动建也行：打开 https://gist.github.com ，文件名必须一字不差，内容取自 `dist/` 下同名文件。

选 `Create secret gist` 或 `public gist` 都行——secret 不在列表里展示，但 raw 地址照样匿名可拉。

### 2. 已有 Gist 时重建 + 校验（一条命令）

```bash
python deploy-gist.py --raw "https://gist.githubusercontent.com/<user>/<id>/raw/<sha>/adclean.js"
```

带 sha 的地址会被自动规范化成 `/raw/adclean.js`，这样以后改 Gist 内容地址不变、模块不用重导。`splash.js` 从同目录推导，脚本会先验两个 URL 真能拉到、再调 `build.py --verify` 重建模块。


### 3. 导模块

不用手工加。`python deploy-gist.py --auto` 会把**五个文件**一起推上去：

| 文件 | 手机上拿来干嘛 |
|---|---|
| `adclean.js` | 信息流清洗脚本 |
| `splash.js` | 开屏清洗脚本 |
| `adblock-lite.module` | 纯规则版，模块订阅 |
| `adblock-pro.module` | 完整版，模块订阅 |
| `adblock-pro-plus.module` | 外链版，模块订阅 |

推完模块地址是：

```
https://gist.githubusercontent.com/<user>/<id>/raw/adblock-pro-plus.module
```

在小火箭里「配置 > 模块 > 右上角 + > 填地址 > 下载」，就是订阅。之后本机 `serve.py` 可以永久关掉——手机直接从 Gist 拿。

### 4. 以后更新

**改了脚本或规则，一条命令**：

```bash
python build.py
python deploy-gist.py --update <gist-id>
```

`--update` 是原地 PATCH，只换文件内容，raw 地址不变，手机上的订阅地址永远有效。

**别用 `--auto` 反复建新 Gist** —— 每建一次都是新地址，手机拿的还是旧的。`--auto` 只在该建的时候用一次。

## 自动更新：三层能自动到什么程度

小火箭的「自动更新」只刷新**远程资源**（规则集、脚本），不会重新下载模块本身。所以三层要分开看：

| 层 | 现状 | 自动刷新 |
|---|---|---|
| 脚本 `adclean.js` / `splash.js` | 挂在 Gist | ✅ 会 |
| `core.list` 165 条手写规则 | 硬编码在模块 `[Rule]` | ❌ 要重导模块 |
| `auto.list` 机器抓的规则 | 硬编码在模块 `[Rule]`，由每日构建重写 | 走下面的每日构建 |
| 外链域名集（plus 版 `DOMAIN-SET`） | 指向 anti-AD 全量列表（6.8 万条） | ✅ 会 |

### 开启自动更新

手机 `设置` → `自动更新`：

- **配置** → `自动后台更新` 打开
- **更新间隔** → 1 天（单位就是天，可选 1-7）
- **更新提醒** → 建议打开，出问题能看到

再到 `系统设置 > 通用 > 后台App刷新` 给 Shadowrocket 打开。**这步不做，锁屏后不会跑。**

> 即使用的是本地配置文件，这个开关也会刷新当前用到的规则集和脚本，这是官方手册明确写的。

### 首次加载要等

外链那个 `domains.txt` 是 1.3 MB。这条线在国内很多网络上只有 **2-16 KB/s**，**首次编译配置可能等两三分钟，最坏直接超时拉不完**，期间小火箭界面会像卡死。

实测（同一条宽带）：

```
curl 直连   30s → 489 KB      ≈ 16 KB/s
curl 走代理 30s → 191 KB      ≈  6 KB/s
urllib      8.5 分钟没拉完    < 2.6 KB/s
```

等不下去的话有两个办法：

1. **先导 pro，晚点再换 plus** —— pro 没有任何远程域名集依赖，秒开
2. **自建镜像**：`curl -o domains.txt https://anti-ad.net/domains.txt` 下到自己的服务器或对象存储，然后
   ```bash
   python build.py --upstream-url "https://你的域名/domains.txt"
   ```
   只要格式是「一行一个域名」，`DOMAIN-SET` 就吃，跟 anti-AD 完全兼容

**别因为它第一次慢就以为坏了。** 只要成功编译过一次，后面都走缓存。

### 三个模块怎么选

| 模块 | 规则来源 | 自动更新 | 内存 |
|---|---|---|---|
| `adblock-lite.module` | 165 手写 + 2000 自动 | 每日构建 | 低 ~1 MB |
| `adblock-pro.module` | 165 + 2000 + Gist 脚本 | 脚本 ✅ / 规则靠每日构建 | 中 |
| `adblock-pro-plus.module` | 165 + 2000 + anti-AD 全量 DOMAIN-SET | 脚本 ✅ / 域名集 ✅ / 规则靠每日构建 | 高 |

**plus 版的外链是"每天自己长"的那层**：6.8 万条的列表由 anti-AD 每天重建，小火箭按你设的间隔去拉。代价是 NE 内存（iOS 15+ 上限 50 MB），设备内存小或者装了一堆别的模块时可能断连。出现断连就换回 pro。

plus 比 pro 多出来的不只是外链，还有 15 条护栏。没有护栏，外链会顺手把你的推送和登录一起杀掉——见上一节。

### 手动强制刷新（不想等自动）

```
配置 > 点当前配置文件 > 编辑配置 > 规则集 URL
  → 点里面的地址重新拉取
  → 返回点「使用配置」或「编译配置」
```

只点 URL 只更新缓存，不重新编译，所以**必须再点一次「使用配置」**才生效。

### 想让它自己长规则：sync.py

`core.list` 是人写的，加一条要联网查 DNS、判断会不会误伤，一周也就几条。
所以另开一条流水线：

```bash
python sync.py            # 抓上游 → 过滤 → 写 src/rules/auto.list
python sync.py --dry-run  # 只看统计，不落盘
python build.py           # auto.list 默认就编进三个模块
```

五个上游源：

| 源 | 规模 | 特点 |
|---|---|---|
| AdGuard Chinese Filter | 223 | 国内广告域，质量高但量小 |
| EasyList China | 9.5k | 国内广告位子域最全 |
| oisd big | 235k | 全球最大的 DNS 黑名单之一 |
| Peter Lowe's adservers | 3.5k | 老牌，稳定 |
| anti-AD | 68k | 国内，每天重建 |

加起来去重后 **24 万条**。全塞进去就是 plus 版干的事，会打残手机。
所以过三道闸门：

**闸门一：保护区**
`guard.list` / `risk.list` 点名的域一票否决，连 `ad.push.example.com` 这种子域都不收。
另外硬编码了一批大厂主域（`qq.com`、`taobao.com`、`apple.com`…）和共享托管域
（`vercel.app`、`github.io`…）—— 后者一个域名下挂几十万个不相关站点，整条拦了会连带一片。

**闸门二：语义 + 交叉验证**

- 多源认可（≥2 个源都收录）→ 收
- 单源但最左标签就是广告词（`ads.xxx.com`、`track.xxx`）→ 收
- 单源且域名本身看不出是广告（`a.baomihua.com`）→ 进 B 档，默认不启用

**闸门三：垃圾规则清出**

- 随机落地页域名（`00k456wyh.com`、`a1ajfrk4i.com`）→ 丢。
  这类在 EasyList 里成片出现，但生命周期只有几天到几周，静态模块收了也是白占内存。
  覆盖它们本来就是 plus 版外链的活。
- 纯数字标签（`||1.example.com^` 这种泛匹配残留）→ 丢
- 超长域名（>63 字节）→ 丢

跑完的结果：24 万 → **A 档 2000 条**（编进模块）+ **B 档 2800 条**（`--with-auto-extra` 才用）。

```
抓到去重域名：242207 条
  -单源且最左标签无广告词   212680
  -保护区                18181
  -随机落地页域（交给 plus 外链）  3020
  -超长                    897
  -纯数字标签（泛匹配残留）       53
  =A 档 auto.list        4080   →  截断到 2000
  =B 档 auto-extra.list  2856
```

排序有个细节：`.cn` 域名优先。国内 App 的广告位基本都在 cn 下，
而它们大多只被 EasyList China 一个源收录，按「源数降序」排会被海外多源域名挤到截断线外。

**粘性**：上一轮抓到、这一轮临时没抓到的域名默认保留，不会忽有忽无。
要清就用 `--prune`。

`auto.list` 是生成物，别手改 —— 下次 sync 直接覆盖。
想让某条永远别进来，写进 `guard.list`；想手工加，写 `core.list`。

### 每日自动跑

两条路，按有没有 GitHub 仓库选。

**A. 本机计划任务（不需要仓库）**

```powershell
powershell -ExecutionPolicy Bypass -File daily-build.ps1 -Install
```

注册每天 04:00 跑一次：抓上游 → 构建 → 写 `logs/daily.log`。
`-Uninstall` 卸载，不带参数就是立刻跑一次，`-NoSync` 跳过抓取只重建。

构建完 `dist/` 就是最新的。手机还是得重导一次 —— 除非配了 Gist：

```powershell
$env:GIST_TOKEN = "ghp_xxx"     # classic PAT，勾 gist
$env:GIST_ID    = "115df6b1947d22f7d356f594f0ce1223"
powershell -ExecutionPolicy Bypass -File daily-build.ps1
```

配了之后模块文件也会推上 Gist，手机订阅这个地址就能自动更新：

```
https://gist.githubusercontent.com/<user>/<gist-id>/raw/adblock-pro-plus.module
```

`deploy-gist.py --update <gist-id>` 是原地更新，raw 地址不变。**别建新 Gist** ——
建新的会让手机上的地址指向旧内容，等于永远拿不到更新。

**B. GitHub Actions（不依赖本机开机）**

仓库根目录已经有 `.github/workflows/daily.yml`，推上去就生效：

- 每天 UTC 20:00（北京 04:00）跑
- 抓规则 → 构建 → 跑单测 → 有变化才 commit
- 提交之后 `raw.githubusercontent.com/<user>/<repo>/main/dist/adblock-pro.module`
  就是最新的，手机订阅这个地址

要它顺手更新 Gist，就在仓库 Settings 里配两个东西：

| 类型 | 名字 | 值 |
|---|---|---|
| Secret | `GIST_TOKEN` | classic PAT，勾 gist |
| Variable | `GIST_ID` | 你的 Gist id |
| Variable | `SCRIPT_URL` | 可选，adclean.js 的 raw 地址 |

不配也能跑，只是不碰 Gist。

**单测会拦住事故**：`node test/test.js` 17 项 + `test-splash.js` 14 项，
改崩了广告清洗逻辑，构建直接失败，不会推一份坏规则上去。

### 还是手动改更稳？

行，那就：

- 需要新域名时，改 `src/rules/core.list`
- `python build.py`
- 手机重导一次模块

`auto.list` 想说不要就不要：`python build.py --no-auto`。

