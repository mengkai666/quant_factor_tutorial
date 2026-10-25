# 📱 quant_factor_mobile (Flutter 移动端工程)

**A 股量化投研与主线强度追踪终端 · 跨平台原生 App (Android / iOS / Web / 桌面)**

采用 **Material 3 极简深色终端设计 (Obsidian FinTech Dark)**，针对触屏深度优化，内嵌 **TradingView Lightweight Charts** 60 FPS 手势触控 K 线图。

---

## ✨ 核心特性

1. **Material 3 极简设计规范**：
   - 适配深色 OLED 屏幕（`#0B0E14` 曜石黑），无刺眼背景；
   - 遵从 A 股交易直觉（红涨绿跌、多空状态呼吸灯：🟢 顺势绿灯 / 🟡 震荡黄灯 / 🔴 禁区红灯）。
2. **触屏手势专属 K 线图 (TradingView Lightweight Charts)**：
   - 双指捏合自由缩放（Pinch-to-zoom）、单指平移拖拽、长按十字光标锁定 Tooltip；
   - 蜡烛图叠加 **EMA5（天蓝）** 与 **EMA20（明黄）** 均线与成交量副图。
3. **真实 API 深度融合**：
   - 开盘啦：涨停连板天梯（从 8B、4B 龙头到首板）、盘面异动梳理时间线；
   - 财联社：全市场情绪温度计（0-100度）、两市成交额与缩放增量、主线机会解读、今日风口。
4. **两级容灾与离线缓存**：
   - 自动嗅探局域网 WiFi IP (`192.168.1.x`) 与本地模拟器回环地址；
   - 具备 `SharedPreferences` 本地离线持久化存储，弱网/断网环境下秒开历史复盘。

---

## 📁 目录结构

```
flutter_app/
├── pubspec.yaml                 # 依赖配置 (webview_flutter / http / shared_preferences)
├── lib/
│   ├── main.dart                # App 入口 (沉浸式状态栏配置)
│   ├── theme/
│   │   └── app_theme.dart       # Material 3 深色主题 (Obsidian FinTech Palette)
│   ├── models/
│   │   └── bundle_model.dart    # 强类型数据模型 (情绪、主线、自选股、交易预案)
│   ├── services/
│   │   └── quant_api_service.dart# 网络服务与离线缓存 (自动切换局域网候选 IP)
│   ├── widgets/
│   │   ├── tradingview_kline_widget.dart # 触控 TradingView K 线图组件 (WebView 嵌入)
│   │   ├── mood_gauge_widget.dart        # 情绪温度仪表盘与进度条
│   │   ├── distribution_chart_widget.dart# 市场涨跌分布区间直方图
│   │   ├── status_lamp_widget.dart       # 多空状态呼吸灯
│   ├── screens/
│   │   ├── home_scaffold.dart   # 底部 4-Tab 导航骨架与下拉刷新
│   │   ├── tab_mood_screen.dart # Tab 1: 大盘情绪周期
│   │   ├── tab_mainline_screen.dart # Tab 2: 抱团主线与风口
│   │   ├── tab_focus_screen.dart# Tab 3: 核心自选池与 K 线图
│   │   └── tab_strategy_screen.dart # Tab 4: 实战推演预案
```

---

## 🚀 编译与运行指南

### 1. 确保数据服务已启动
在主项目根目录下启动 Python 数据汇聚服务：
```bash
python tools/run_mobile_server.py
```
服务将在后台监听 `0.0.0.0:8088`，并实时抓取开盘啦与财联社数据。

### 2. 运行 Flutter 客户端
进入 `flutter_app` 目录：
```bash
cd flutter_app

# 获取依赖
flutter pub get

# 连上 Android / iPhone 手机或模拟器后直接运行
flutter run
```

### 3. 打包 Android 独立安装包 (.apk)
```bash
flutter build apk --release
```
打包产物将位于：`flutter_app/build/app/outputs/flutter-apk/app-release.apk`，可直接传输到安卓手机安装使用。
