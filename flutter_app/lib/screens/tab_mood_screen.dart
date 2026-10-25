import 'package:flutter/material.dart';
import '../models/bundle_model.dart';
import '../theme/app_theme.dart';
import '../widgets/mood_gauge_widget.dart';
import '../widgets/distribution_chart_widget.dart';

/// Tab 1: 大盘情绪周期
class TabMoodScreen extends StatelessWidget {
  final TabMood mood;

  const TabMoodScreen({super.key, required this.mood});

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        // 1. 情绪温度计
        MoodGaugeWidget(
          degree: mood.degree,
          statusText: mood.statusText,
          colorTag: mood.colorTag,
          turnover: mood.turnover,
          turnoverChange: mood.turnoverChange,
        ),
        const SizedBox(height: 12),

        // 2. 赚钱效应与分布直方图
        DistributionChartWidget(
          upCount: mood.upCount,
          downCount: mood.downCount,
          ztCount: mood.ztCount,
          dtCount: mood.dtCount,
          brokenRatio: mood.brokenRatio,
          bins: mood.distributionBins,
        ),
        const SizedBox(height: 12),

        // 3. 连板高度天梯
        _buildTiantiCard(),
        const SizedBox(height: 12),

        // 4. 盘面异动梳理时间线
        _buildPanmianCard(),
        const SizedBox(height: 32),
      ],
    );
  }

  Widget _buildTiantiCard() {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppTheme.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppTheme.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Text(
                '连板天梯梯队 (开盘啦实时)',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: AppTheme.textMuted,
                ),
              ),
              if (mood.tiantiStocks.isNotEmpty)
                Text(
                  '最高 ${mood.tiantiStocks.first.height} 板',
                  style: const TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    color: AppTheme.riseRed,
                  ),
                ),
            ],
          ),
          const SizedBox(height: 12),
          if (mood.tiantiStocks.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 16),
              child: Center(
                child: Text('暂无连板梯队数据', style: TextStyle(color: AppTheme.textMuted, fontSize: 12)),
              ),
            )
          else
            ...mood.tiantiStocks.take(12).map((s) => _buildTiantiRow(s)),
        ],
      ),
    );
  }

  Widget _buildTiantiRow(TiantiStock stock) {
    final isHigh = stock.height >= 4;
    final bg = isHigh
        ? AppTheme.riseRed.withOpacity(0.25)
        : (stock.height >= 2 ? AppTheme.riseRed.withOpacity(0.12) : AppTheme.surfaceSub);
    final color = stock.height >= 2 ? AppTheme.riseRed : AppTheme.textPrimary;

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          Container(
            width: 34,
            padding: const EdgeInsets.symmetric(vertical: 3),
            decoration: BoxDecoration(
              color: bg,
              borderRadius: BorderRadius.circular(6),
            ),
            alignment: Alignment.center,
            child: Text(
              '${stock.height}B',
              style: TextStyle(fontSize: 11, fontWeight: FontWeight.w800, color: color),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      stock.name,
                      style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w700, color: Colors.white),
                    ),
                    Text(
                      stock.plateName,
                      style: const TextStyle(fontSize: 11, color: AppTheme.textMuted),
                    ),
                  ],
                ),
                const SizedBox(height: 2),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      stock.code,
                      style: const TextStyle(fontSize: 11, color: AppTheme.textMuted, fontFamily: 'monospace'),
                    ),
                    Text(
                      '流通: ${(stock.floatMarketVal / 100000000).toStringAsFixed(1)}亿',
                      style: const TextStyle(fontSize: 11, color: AppTheme.textMuted),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildPanmianCard() {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppTheme.surface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: AppTheme.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Text(
                '盘面异动梳理 (实时事件)',
                style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: AppTheme.textMuted),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: AppTheme.accentSky.withOpacity(0.15),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: const Text('时间线', style: TextStyle(fontSize: 10, color: AppTheme.accentSky)),
              ),
            ],
          ),
          const SizedBox(height: 10),
          if (mood.panmianEvents.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 12),
              child: Center(
                child: Text('今日无明显异动事件', style: TextStyle(color: AppTheme.textMuted, fontSize: 12)),
              ),
            )
          else
            ...mood.panmianEvents.take(6).map((e) => Container(
                  margin: const EdgeInsets.only(bottom: 8),
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: AppTheme.surfaceSub,
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: AppTheme.border),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Text(
                            '[${e.tagName}] ${e.plateName}',
                            style: TextStyle(
                              fontSize: 11,
                              fontWeight: FontWeight.w700,
                              color: e.tagType == 0 ? AppTheme.fallGreen : AppTheme.riseRed,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 3),
                      Text(
                        e.detail,
                        style: const TextStyle(fontSize: 11, color: AppTheme.textPrimary, height: 1.4),
                      ),
                    ],
                  ),
                )),
        ],
      ),
    );
  }
}
