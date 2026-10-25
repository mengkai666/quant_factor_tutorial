import 'package:flutter/material.dart';
import '../models/bundle_model.dart';
import '../theme/app_theme.dart';
import '../widgets/status_lamp_widget.dart';
import '../widgets/tradingview_kline_widget.dart';

/// Tab 3: 核心自选池 (Focus Pool) 与交互式 K 线图
class TabFocusScreen extends StatefulWidget {
  final List<FocusStock> focusList;

  const TabFocusScreen({super.key, required this.focusList});

  @override
  State<TabFocusScreen> createState() => _TabFocusScreenState();
}

class _TabFocusScreenState extends State<TabFocusScreen> {
  int? _expandedIndex;

  @override
  void initState() {
    super.initState();
    // 默认展开首只标的的 K 线图，方便直观体验
    if (widget.focusList.isNotEmpty) {
      _expandedIndex = 0;
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        // 顶部提示条
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(
            color: AppTheme.accentSky.withOpacity(0.08),
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: AppTheme.accentSky.withOpacity(0.2)),
          ),
          child: const Row(
            children: [
              Icon(Icons.lightbulb_outline, size: 16, color: AppTheme.accentSky),
              SizedBox(width: 8),
              Expanded(
                child: Text(
                  '关注池自动对齐 focus_pool.csv 与前排龙头，点击卡片展开 TradingView 触控 K 线图。',
                  style: TextStyle(fontSize: 11, color: AppTheme.accentSky),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),

        if (widget.focusList.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 32),
            child: Center(
              child: Text('暂无关注池标的', style: TextStyle(color: AppTheme.textMuted, fontSize: 13)),
            ),
          )
        else
          ...widget.focusList.asMap().entries.map((entry) {
            final idx = entry.key;
            final stock = entry.value;
            final isExpanded = _expandedIndex == idx;
            return _buildStockCard(stock, idx, isExpanded);
          }),
        const SizedBox(height: 32),
      ],
    );
  }

  Widget _buildStockCard(FocusStock stock, int index, bool isExpanded) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppTheme.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: isExpanded ? AppTheme.accentSky.withOpacity(0.4) : AppTheme.border,
          width: isExpanded ? 1.5 : 1.0,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 标的头部信息
          GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: () {
              setState(() {
                _expandedIndex = isExpanded ? null : index;
              });
            },
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Text(
                          stock.name,
                          style: const TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.w700,
                            color: Colors.white,
                          ),
                        ),
                        const SizedBox(width: 6),
                        Text(
                          stock.code,
                          style: const TextStyle(
                            fontSize: 11,
                            fontFamily: 'monospace',
                            color: AppTheme.textMuted,
                          ),
                        ),
                        const SizedBox(width: 6),
                        if (stock.isCustom)
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                            decoration: BoxDecoration(
                              color: AppTheme.accentSky.withOpacity(0.15),
                              borderRadius: BorderRadius.circular(4),
                            ),
                            child: const Text('核心池', style: TextStyle(fontSize: 9, color: AppTheme.accentSky)),
                          )
                        else
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                            decoration: BoxDecoration(
                              color: AppTheme.riseRed.withOpacity(0.15),
                              borderRadius: BorderRadius.circular(4),
                            ),
                            child: Text('${stock.height}B', style: const TextStyle(fontSize: 9, color: AppTheme.riseRed)),
                          ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    Text(
                      '${stock.sector} · ${stock.poolType}',
                      style: const TextStyle(fontSize: 11, color: AppTheme.textMuted),
                    ),
                  ],
                ),
                StatusLampWidget(status: stock.status),
              ],
            ),
          ),
          const SizedBox(height: 12),

          // 核心执行逻辑卡片
          Container(
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: AppTheme.surfaceSub,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: AppTheme.border),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  '🎯 入场条件：',
                  style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: AppTheme.riseRed),
                ),
                const SizedBox(height: 2),
                Text(
                  stock.entryRule,
                  style: const TextStyle(fontSize: 11, color: AppTheme.textPrimary, height: 1.4),
                ),
                const SizedBox(height: 6),
                const Text(
                  '🛡️ 防守纪律：',
                  style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: AppTheme.warningAmber),
                ),
                const SizedBox(height: 2),
                Text(
                  stock.defenseRule,
                  style: const TextStyle(fontSize: 11, color: AppTheme.textMuted, height: 1.4),
                ),
              ],
            ),
          ),

          // 展开状态下的触控 K 线图
          if (isExpanded) ...[
            const SizedBox(height: 12),
            TradingViewKlineWidget(
              stockCode: stock.code,
              stockName: stock.name,
              height: 260,
              candles: stock.candles,
              ema5: stock.ema5,
              ema20: stock.ema20,
            ),
          ],
        ],
      ),
    );
  }
}
