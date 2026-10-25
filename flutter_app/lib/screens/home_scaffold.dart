import 'package:flutter/material.dart';
import '../models/bundle_model.dart';
import '../services/quant_api_service.dart';
import '../theme/app_theme.dart';
import 'tab_mood_screen.dart';
import 'tab_mainline_screen.dart';
import 'tab_focus_screen.dart';
import 'tab_strategy_screen.dart';

/// 移动端主导航骨架 (Material 3 NavigationBar + Pull to Refresh)
class HomeScaffold extends StatefulWidget {
  const HomeScaffold({super.key});

  @override
  State<HomeScaffold> createState() => _HomeScaffoldState();
}

class _HomeScaffoldState extends State<HomeScaffold> {
  int _currentIndex = 0;
  QuantBundle? _bundle;
  bool _isLoading = true;
  String? _errorMessage;

  final List<String> _titles = [
    '大盘情绪周期',
    '抱团与主线机会',
    '核心自选池 (Focus)',
    '实战推演与预案',
  ];

  @override
  void initState() {
    super.initState();
    _loadData();
  }

  Future<void> _loadData() async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    try {
      final bundle = await QuantApiService.fetchBundle();
      setState(() {
        _bundle = bundle;
        _isLoading = false;
      });
    } catch (e) {
      setState(() {
        _errorMessage = e.toString();
        _isLoading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(_titles[_currentIndex]),
            Text(
              _bundle != null
                  ? '${_bundle!.meta.date} · 更新于 ${_bundle!.meta.updatedAt.split(' ').length > 1 ? _bundle!.meta.updatedAt.split(' ')[1] : ''}'
                  : '正在同步量化数据...',
              style: const TextStyle(fontSize: 11, color: AppTheme.textMuted, fontWeight: FontWeight.normal),
            ),
          ],
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh, color: Colors.white, size: 20),
            tooltip: '刷新数据',
            onPressed: _loadData,
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: _buildBody(),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _currentIndex,
        onDestinationSelected: (idx) {
          setState(() => _currentIndex = idx);
        },
        destinations: const [
          NavigationDestination(
            icon: Icon(Icons.flash_on_outlined),
            selectedIcon: Icon(Icons.flash_on),
            label: '情绪',
          ),
          NavigationDestination(
            icon: Icon(Icons.hub_outlined),
            selectedIcon: Icon(Icons.hub),
            label: '主线',
          ),
          NavigationDestination(
            icon: Icon(Icons.show_chart_outlined),
            selectedIcon: Icon(Icons.show_chart),
            label: '自选K线',
          ),
          NavigationDestination(
            icon: Icon(Icons.description_outlined),
            selectedIcon: Icon(Icons.description),
            label: '预案',
          ),
        ],
      ),
    );
  }

  Widget _buildBody() {
    if (_isLoading) {
      return const Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            CircularProgressIndicator(strokeWidth: 2, color: AppTheme.accentSky),
            SizedBox(height: 16),
            Text('正在加载最新量化行情...', style: TextStyle(color: AppTheme.textMuted, fontSize: 13)),
          ],
        ),
      );
    }

    if (_errorMessage != null && _bundle == null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.cloud_off, size: 48, color: AppTheme.warningAmber),
              const SizedBox(height: 16),
              Text(
                _errorMessage!,
                textAlign: TextAlign.center,
                style: const TextStyle(color: AppTheme.textMuted, fontSize: 13),
              ),
              const SizedBox(height: 20),
              ElevatedButton.icon(
                onPressed: _loadData,
                icon: const Icon(Icons.refresh, size: 16),
                label: const Text('重试连接'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: AppTheme.surfaceSub,
                  foregroundColor: Colors.white,
                  side: const BorderSide(color: AppTheme.border),
                ),
              ),
            ],
          ),
        ),
      );
    }

    return RefreshIndicator(
      color: AppTheme.accentSky,
      backgroundColor: AppTheme.surfaceSub,
      onRefresh: _loadData,
      child: IndexedStack(
        index: _currentIndex,
        children: [
          TabMoodScreen(mood: _bundle!.mood),
          TabMainlineScreen(crowding: _bundle!.crowding),
          TabFocusScreen(focusList: _bundle!.focusList),
          TabStrategyScreen(strategy: _bundle!.strategy),
        ],
      ),
    );
  }
}
