# trackpoint-phantom-middle

**在这台 ThinkPad 上，按任何一个键都会触发一次粘贴。** 这是它的修复方案：让小红点
完全照常工作，只掐掉那一个造成粘贴的幽灵事件。

*[English →](README.md)*

> **面向并已验证于：Ubuntu 26.04 + ThinkPad X1 Carbon Gen 8**
> （20UASDGA00，内核 7.0.0-34）。
>
> 底层病根 i8042 字节串扰**不是这一款机型、也不是这一个发行版独有的**，其他 ThinkPad
> 和其他发行版同样可能中招。但这个组合是本项目的开发环境，也是已知能正常工作的组合。
> 别急着对号入座，先看下面的[根因](#根因)一节确认症状是否一致。

## 症状

受影响的 ThinkPad 上，在终端里打字会粘贴主选区：按一下 `a`，剪贴板内容冒出来。
小红点手感可能也发飘，Backspace / Tab 这类键偶尔会卡住。

## 根因

键盘（`i8042 KBD`，`serio0`）与 TrackPoint（`i8042 AUX`，`serio1`，本机是
`TPPS/2 Elan TrackPoint`）**共用同一个 i8042 控制器**。在这类机器上字节发生串扰，
结果是 TrackPoint 的 `BTN_MIDDLE` 变成了键盘事件流的**反转镜像**：

| 你做的事 | TrackPoint 报出来的 |
|---|---|
| 按下某键 | `BTN_MIDDLE` 松开 |
| 松开某键 | `BTN_MIDDLE` 按下 |

它永远不平息，而每一次幽灵按下都是一次中键点击——Linux 下中键点击即粘贴主选区。
于是：打字就粘贴。

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

幽灵中键和实体中键是同一台设备上的同一个事件码，无从区分，所以 `BTN_MIDDLE` 被整体
丢弃：**实体中键失效——既不粘贴也不滚动**。这是换来小红点可用的代价。如果你更想留住
中键，本机上唯一有效的替代方案是干脆不读 TrackPoint，而那样小红点也没了。

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

i8042/EC 的字节串扰本身没有动，被过滤掉的只有"中键"这一路症状。在开发所用的这台
机器上，另外两个症状仍在：

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
