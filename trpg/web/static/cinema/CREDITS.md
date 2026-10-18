# AI 剧场静态资产 · 来源与处理

本目录的 JPG 由 `tools/build_cinema_assets.py` 生成。运行游戏不需要联网，
也不需要 `.reference/` 里的 PDF；这些 JPG 已经随仓库分发。

## 官方免费素材

- 来源：R. Talsorian Games 免费发布的 *Cyberpunk RED Easy Mode* 中文手册
  （`.reference/Cyberpunk_RED_Easy_Mode_CN.pdf`，本地参考文件，不入库）。
- 使用文件：
  - `portrait_forty.jpg` ← 第 41 页 Forty 角色画
  - `portrait_mover.jpg` ← 第 43 页 Mover 角色画
  - `portrait_torch.jpg` ← 第 45 页 Torch 角色画
  - `portrait_redtail.jpg` ← 第 47 页 Redtail 角色画
  - `portrait_media.jpg` ← 第 49 页 24/7 角色画
  - `bg_title.jpg` / `bg_dawn.jpg` ← 第 1 页封面插画
  - `bg_street.jpg` ← 第 51 页夜之城街景
  - `bg_alley.jpg` / `bg_ambush.jpg` ← 第 4 页战斗插画
  - `bg_concert.jpg` ← 第 18 页演唱会插画（终局/备用底片）
- 处理：裁切、旋转、去边、调色（低饱和/冷影暖高光）、半调网点、纸纹、
  颗粒、暗角。没有修改角色设定或伪造官方内容。

## 程序化绘景

- `bg_loft.jpg`、`bg_room.jpg`、`bg_warehouse.jpg`（以及未被官方图覆盖时的
  `bg_alley.jpg` / `bg_dawn.jpg` 备选）由 `tools/build_cinema_assets.py` 用
  Pillow 绘制：渐变、透视、笔触、雨、体积光、颗粒、暗角。
- 纸纹、网点、CSS 雨幕、颗粒、扫描线由 `cinema.css` 的 SVG/CSS 运行时生成。

## 字体与音频

- 不打包商业字体；使用系统衬线/无衬线字体回退。
- 环境音与事件音全部由 Web Audio 实时合成（`cinema.js` + `fx.js`），
  不含第三方音频文件。

## 授权

官方美术版权归 R. Talsorian Games 所有；Cyberpunk® 是 CD Projekt Red S.A.
的注册商标。本项目为非官方同人作品，官方美术仅用于非商业同人展示，不随代码的
MIT 许可授权。详见仓库根 `README.md`「发行与许可」与 `NOTICE.md`。
