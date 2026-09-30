# trackpoint-phantom-middle

**在这台 ThinkPad 上，按任何一个键都会触发一次粘贴。** 这是它的修复方案：让小红点
完全照常工作，只掐掉那一个造成粘贴的幽灵事件。

*[English →](README.md)*

> **面向并已验证于：Ubuntu 26.04 + ThinkPad X1 Carbon Gen 8**
> （20UASDGA00，内核 7.0.0-34）。
>
> 底层病根在嵌入式控制器（EC）里、在 Linux 之下，**不是这一款机型、也不是这一个发行版
> 独有的**，其他 ThinkPad 和其他发行版同样可能中招。但这个组合是本项目的开发环境，也是
> 已知能正常工作的组合。别急着对号入座，先看下面的[根因](#根因)一节确认症状是否一致。

## 症状

受影响的 ThinkPad 上，在终端里打字会粘贴主选区：按一下 `a`，剪贴板内容冒出来。
小红点手感可能也发飘，Backspace / Tab 这类键偶尔会卡住。

## 根因

键盘（`i8042 KBD`，`serio0`）与 TrackPoint（`i8042 AUX`，`serio1`，本机是
`TPPS/2 Elan TrackPoint`）**共用同一个 i8042 控制器**，结果是 TrackPoint 的
`BTN_MIDDLE` 变成了键盘事件流的**反转镜像**：

| 你做的事 | TrackPoint 报出来的 |
|---|---|
| 按下某键 | `BTN_MIDDLE` 松开 |
| 松开某键 | `BTN_MIDDLE` 按下 |

它永远不平息，而每一次幽灵按下都是一次中键点击——Linux 下中键点击即粘贴主选区。
于是：打字就粘贴。

### 它到底从哪来

最容易想到的解释——也是这份 README 原先写的——是「共用的控制器把两个口搞串了」。
**并不是。i8042 在这一环上是个忠实的搬运工。**

打开字节级 trace 然后打字：

```bash
echo 1 | sudo tee /sys/module/i8042/parameters/debug
sudo dmesg -w
```

键盘字节只会以 `(interrupt, 0, 1)` 出现，TrackPoint 字节只会以 `(interrupt, 1, 12)`
出现，都是严格成帧的 3 字节包，**没有一个字节投递到错误的口上**。出现在 AUX 口上的，
是一个完好成形、但没人请求过的鼠标包：一对跟着每次击键蹭车进来的幽灵按下／松开。

所以这个包是**在内核之下、由 EC/TrackPoint 固件合成的**。这解释了为什么内核侧、驱动侧
的所有绕法从来都没用，也解释了为什么换内核版本或 flavour 都修不好：缺陷在 Linux 能
经手的任何东西的上游。

你可以先自己确认这个特征，什么都不用装：

```bash
sudo ./install.sh observe
```

它不独占设备，只打印原始事件流。一边打字一边看：如果 `BTN_MIDDLE` 严格跟着你的
按键一按一放，那这个项目就是给你准备的。

## 修复

用 `EVIOCGRAB` 独占 TrackPoint，再通过 `uinput` **逐事件**重建，逐个决定去留：

| 事件 | 处理 | 结果 |
|---|---|---|
| `EV_REL` `REL_X` / `REL_Y` | 转发 | 小红点照常移动光标 |
| `BTN_LEFT` / `BTN_RIGHT` | 转发 | 左右实体键正常 |
| `BTN_MIDDLE` | **丢弃** | 打字不再粘贴 |

桌面看到的是一台叫 `TrackPoint Middle Filtered` 的新设备，真设备被守护进程独占。

### 代价，说清楚

幽灵中键和实体中键是同一台设备上的同一个事件码，所以 `BTN_MIDDLE` 被整体丢弃：
**实体中键失效——既不粘贴也不滚动**。这是换来小红点可用的代价。

要说清楚的是：这**不是**本滤波器不够聪明、换个更好的过滤逻辑就能改善的事。硬件本身
就**根本不上报这个键**，见下节。

如果你更想留住中键，本机上唯一有效的替代方案是干脆不读 TrackPoint，而那样小红点也没了。

### 实体中键：确定没了

短版本：在这台机器上，TrackPoint 的中键位**不携带可用信息**，没有东西可以拿来当判据。
用字节级 trace 实测（`i8042.debug=1`；TrackPoint 字节从不被掩码，所以不会暴露按键）。
**原始抓取文件和分析脚本就在 [`evidence/`](evidence/README.md)**——数字摆在那儿，自己去验：

- 推小红点 **10.5 秒**，产生 **948 个包，948 个全部**把中键位置为 1——期间位移数值各种变化，
  这一位**一次都没清过**。（更早一轮是 621 个包，结论相同。）
- **静止时按键**（不碰杆子）**一个字节都不产生**：五次按键落在 **49.5 秒的完全静默**里。
  更早一轮也把约八次按键和长按关在 95.5 秒的静默里。如果实体按键真能上报，每次按键都该
  自成一个小区段——一个按下包、一个松开包，无位移、附近无击键。**两轮里都不存在这样的段。**
- 中键位为 0 的包，只有打字那一段里的 **24 个中的 12 个**，且与击键一一对应——那是幽灵，
  不是按键。

也就是说，这一位是被「移动」**置起来的**，而静止时按键连包都不产生。所以它不属于那个按键，
它属于固件：平时保持 1，只在合成幽灵时才翻出一个 0 再翻回去。

**实体按键根本没有连到这份上报上。** 丢弃 `BTN_MIDDLE` 因此不是策略取舍——**线上没有这个
信号可依据**。不只是人们真正需要中键的那个动作（**按住它推杆当滚轮**，那本身就是移动），
连静止点一下也没有可用的签名。

这也**不是**「等固件修」的 bug。这台机器出厂是 2021 年的 BIOS 1.17 + EC 0.1.10；我们刷到联想
最新一版——**BIOS `N2WET52W` 1.42**（2026 年）/ **EC 0.1.15**（`fwupdmgr` 刷写）——重启后
重跑同一套字节级抓取，**逐包一致**：移动期间该位依旧全程为 1，静止按中键那段依旧完全静默，
幽灵对依旧一次击键一对。跨了近五年的固件版本毫无区别——如果这个按键压根没接到上报上，
这正是应有的结果。

### 为什么网上那几种办法都不行

它们全都是**设备级开关**。幽灵事件和正常移动来自同一台设备，所以在这些方案下
"小红点能用"和"不犯病"必然互斥。这个项目存在的全部理由就是这个。

| 试过的 | 为什么不行 |
|---|---|
| `blacklist psmouse` | 内核不再读 TrackPoint，小红点死 |
| udev `LIBINPUT_IGNORE_DEVICE` | 同上，只是发生在 libinput 层 |
| `xinput disable` | 同上，只是发生在 X 层 |
| `gsettings ... gtk-enable-primary-paste false` | 本来就是 false；GTK4/VTE 自己实现中键粘贴，不认这个键 |
| `echo serio1 > .../psmouse/unbind` | 不持久，psmouse 几秒内自己绑回来 |
| `psmouse smartscroll=` | 无关，那是罗技滚轮的自动重复 |

## 安装

```bash
git clone https://github.com/zionfuo/trackpoint-phantom-middle
cd trackpoint-phantom-middle
./install.sh
```

`install.sh` 需要 `sudo`。它会自动探测你机器上 TrackPoint 的设备名、装
`python3-evdev`、渲染 systemd unit 并启动服务。探测失败时手动指定：

```bash
TRACKPOINT_NAME='TPPS/2 IBM TrackPoint' ./install.sh
```

本机当前的设备名，供参考：

```
N: Name="TPPS/2 Elan TrackPoint"
P: Phys=isa0060/serio1/input0
H: Handlers=mouse3 event9
```

## 验证

```bash
./install.sh status              # 服务状态 + 最近日志
journalctl -u trackpoint-phantom-middle -f
```

四件事都要成立：

1. 终端里打字**不再粘贴**；
2. 推小红点，光标**照常移动**；
3. 左右实体键**正常**；
4. 日志里 `dropped N phantom BTN_MIDDLE events` 随打字**增长**——这才证明过滤真的
   在干活，而不是症状碰巧没发作。

第 4 点很重要。这个毛病看起来就是时有时无的；没有这个计数器，你分不清"修好了"和
"这会儿正好没犯"。

## 排错

- **没反应 / 日志说 "waiting for ... to appear"。** unit 里的设备名和你的硬件对不上。
  用 `cat /proc/bus/input/devices` 找真名，然后要么带 `TRACKPOINT_NAME=...` 重跑
  `install.sh`，要么直接改
  `/etc/systemd/system/trackpoint-phantom-middle.service` 里的
  `Environment="TRACKPOINT_NAME=..."`，再
  `systemctl daemon-reload && systemctl restart trackpoint-phantom-middle`。
  守护进程是**轮询等设备**而不是退出，所以开机顺序、suspend/resume、i8042 重新探测
  都能自愈。
- **`Environment=` 的引号是必需的。** systemd 会按空白切分不加引号的值：
  `Environment=TRACKPOINT_NAME=TPPS/2 Elan TrackPoint` 会静默变成 `TPPS/2`，于是
  永远匹配不上设备，服务每 2 秒重启一次。
- **要报 bug？** 跑 `./install.sh diagnose`（或 `sudo ./diagnose.sh`），把输出贴出来。

## 卸载

```bash
./install.sh uninstall
```

原状立刻恢复。本项目**从不**写 `/etc/modprobe.d/`，也**从不**写 udev 规则，所以除了
它自己的两个文件之外没有任何东西需要撤销。（它也会顺手清掉本项目旧名字
`reddot-filter.*` 的残留。）

## 这个方案**没**解决的问题

EC 固件合成幽灵包这件事本身没有动——它在 Linux 之下，从这里没法打补丁。被过滤掉的只有
"中键"这一路症状。在开发所用的这台机器上，另外两个症状仍在：

- Backspace / Tab / T / Y / `[` / `]` / C / B 等键会卡在按下状态（表现为终端里喷
  `hhhh`、整段文本重复粘贴）；
- `dmesg` 里有 `atkbd serio0: Unknown key pressed (translated set 2, code 0x5b)`。

同一套 `grab + uinput` 机制理论上也能套到键盘节点上做按键去抖，但得先搞清"卡住的键"
在事件流里长什么样。在那儿靠猜会直接把所有人的打字弄坏。

## 致谢与许可

GPL-3.0-or-later，见 [LICENSE](LICENSE)。

`grab + uinput` 这个机制——以及"在事件层面过滤 TrackPoint"这个整体思路——来自 Zerone
的 **[zo-reddot](https://github.com/Jok0ne/zo-reddot)**。本项目是它的衍生作品，只是把
策略反了过来：上游留住按键、丢掉移动（指针死、按键活）；本项目留住移动、丢掉一个
按键（指针活、中键无）。

也感谢 `python-evdev` 项目，它的对象模型让真正的逻辑只剩大约五十行。
