import 'package:flutter/material.dart';
import '../models/bundle_model.dart';
import '../theme/app_theme.dart';

/// Tab 2: 抱团主线与风口机会
class TabMainlineScreen extends StatelessWidget {
  final TabCrowding crowding;

  const TabMainlineScreen({super.key, required this.crowding});

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        // 1. 市场主线核心机会 (财联社)
        _buildMainlineSection(),
        const SizedBox(height: 12),

        // 2. 今日风口与事件催化
        _buildTuyereSection(),
        const SizedBox(height: 12),

        // 3. 主力资金净流入热力
        _buildFundHeatSection(),
        const SizedBox(height: 32),
      ],
    );
  }

  Widget _buildMainlineSection() {
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
                '市场主线与机会解读 (财联社)',
                style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: AppTheme.textMuted),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: AppTheme.warningAmber.withOpacity(0.15),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: const Text('主线机会', style: TextStyle(fontSize: 10, color: AppTheme.warningAmber)),
              ),
            ],
          ),
          const SizedBox(height: 10),
          if (crowding.mainlines.isEmpty)
            const Text('暂无主线数据', style: TextStyle(color: AppTheme.textMuted, fontSize: 12))
          else
            ...crowding.mainlines.map((m) => Container(
                  margin: const EdgeInsets.only(bottom: 8),
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: AppTheme.surfaceSub,
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: AppTheme.border),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          const Text(
                            '🔥 核心主线',
                            style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700, color: Colors.white),
                          ),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                            decoration: BoxDecoration(
                              color: AppTheme.riseRed.withOpacity(0.15),
                              borderRadius: BorderRadius.circular(4),
                            ),
                            child: const Text('热度 ★★★', style: TextStyle(fontSize: 10, color: AppTheme.riseRed)),
                          ),
                        ],
                      ),
                      const SizedBox(height: 6),
                      Text(
                        m.desc,
                        style: const TextStyle(fontSize: 12, color: AppTheme.textPrimary, height: 1.5),
                      ),
                      if (m.plates.isNotEmpty) ...[
                        const SizedBox(height: 8),
                        Wrap(
                          spacing: 6,
                          runSpacing: 6,
                          children: m.plates
                              .map((p) => Container(
                                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                                    decoration: BoxDecoration(
                                      color: Colors.white.withOpacity(0.06),
                                      borderRadius: BorderRadius.circular(6),
                                    ),
                                    child: Text(
                                      '${p.plateName}: ${p.faucetName} (${p.faucetReason})',
                                      style: const TextStyle(fontSize: 11, color: AppTheme.textMuted),
                                    ),
                                  ))
                              .toList(),
                        ),
                      ],
                    ],
                  ),
                )),
        ],
      ),
    );
  }

  Widget _buildTuyereSection() {
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
                '今日风口与事件催化',
                style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: AppTheme.textMuted),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: AppTheme.riseRed.withOpacity(0.15),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: const Text('突发催化', style: TextStyle(fontSize: 10, color: AppTheme.riseRed)),
              ),
            ],
          ),
          const SizedBox(height: 10),
          if (crowding.tuyeres.isEmpty)
            const Text('暂无突发催化', style: TextStyle(color: AppTheme.textMuted, fontSize: 12))
          else
            ...crowding.tuyeres.map((t) => Container(
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
                      Text(
                        '📌 ${t.title}',
                        style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: AppTheme.accentSky),
                      ),
                      const SizedBox(height: 3),
                      Text(
                        t.interpret,
                        style: const TextStyle(fontSize: 11, color: AppTheme.textMuted, height: 1.4),
                      ),
                    ],
                  ),
                )),
        ],
      ),
    );
  }

  Widget _buildFundHeatSection() {
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
          const Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text(
                '主力资金热力排行',
                style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: AppTheme.textMuted),
              ),
              Text(
                '主力净流入',
                style: TextStyle(fontSize: 11, color: AppTheme.textMuted),
              ),
            ],
          ),
          const SizedBox(height: 8),
          ...crowding.fundHeat.map((f) {
            final isPos = !f.inflowStr.startsWith('-');
            final color = isPos ? AppTheme.riseRed : AppTheme.fallGreen;
            return Padding(
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    f.plateName,
                    style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600, color: Colors.white),
                  ),
                  Row(
                    children: [
                      Text(
                        f.change,
                        style: TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w700,
                          color: color,
                          fontFamily: 'monospace',
                        ),
                      ),
                      const SizedBox(width: 8),
                      Text(
                        '净流入 ${f.inflowStr}',
                        style: const TextStyle(fontSize: 11, color: AppTheme.textMuted),
                      ),
                    ],
                  ),
                ],
              ),
            );
          }),
        ],
      ),
    );
  }
}
