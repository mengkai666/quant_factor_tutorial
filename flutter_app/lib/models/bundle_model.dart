/// 移动端统一数据实体模型 (从 Python 后端 bundle.json 映射)

class QuantBundle {
  final MetaInfo meta;
  final TabMood mood;
  final TabCrowding crowding;
  final List<FocusStock> focusList;
  final StrategyPlan strategy;

  QuantBundle({
    required this.meta,
    required this.mood,
    required this.crowding,
    required this.focusList,
    required this.strategy,
  });

  factory QuantBundle.fromJson(Map<String, dynamic> json) {
    return QuantBundle(
      meta: MetaInfo.fromJson(json['meta'] ?? {}),
      mood: TabMood.fromJson(json['tab_mood'] ?? {}),
      crowding: TabCrowding.fromJson(json['tab_crowding'] ?? {}),
      focusList: (json['tab_focus'] as List? ?? [])
          .map((e) => FocusStock.fromJson(e))
          .toList(),
      strategy: StrategyPlan.fromJson(json['tab_strategy'] ?? {}),
    );
  }
}

class MetaInfo {
  final String version;
  final String updatedAt;
  final String date;

  MetaInfo({required this.version, required this.updatedAt, required this.date});

  factory MetaInfo.fromJson(Map<String, dynamic> json) {
    return MetaInfo(
      version: json['version'] ?? '1.0.0',
      updatedAt: json['updated_at'] ?? '',
      date: json['date'] ?? '',
    );
  }
}

class TabMood {
  final int degree;
  final String statusText;
  final String colorTag;
  final String turnover;
  final String turnoverChange;
  final int upCount;
  final int downCount;
  final int ztCount;
  final int dtCount;
  final double brokenRatio;
  final Map<String, int> distributionBins;
  final List<TiantiStock> tiantiStocks;
  final List<PanmianEvent> panmianEvents;

  TabMood({
    required this.degree,
    required this.statusText,
    required this.colorTag,
    required this.turnover,
    required this.turnoverChange,
    required this.upCount,
    required this.downCount,
    required this.ztCount,
    required this.dtCount,
    required this.brokenRatio,
    required this.distributionBins,
    required this.tiantiStocks,
    required this.panmianEvents,
  });

  factory TabMood.fromJson(Map<String, dynamic> json) {
    final rawBins = json['distribution_bins'] as Map? ?? {};
    final bins = <String, int>{};
    rawBins.forEach((k, v) => bins[k.toString()] = int.tryParse(v.toString()) ?? 0);

    return TabMood(
      degree: json['degree'] ?? 50,
      statusText: json['status_text'] ?? '中性',
      colorTag: json['color_tag'] ?? 'amber',
      turnover: json['turnover'] ?? '—',
      turnoverChange: json['turnover_change'] ?? '',
      upCount: json['up_count'] ?? 0,
      downCount: json['down_count'] ?? 0,
      ztCount: json['zt_count'] ?? 0,
      dtCount: json['dt_count'] ?? 0,
      brokenRatio: (json['broken_ratio'] as num? ?? 0.0).toDouble(),
      distributionBins: bins,
      tiantiStocks: (json['tianti_stocks'] as List? ?? [])
          .map((e) => TiantiStock.fromJson(e))
          .toList(),
      panmianEvents: (json['panmian_events'] as List? ?? [])
          .map((e) => PanmianEvent.fromJson(e))
          .toList(),
    );
  }
}

class TiantiStock {
  final String code;
  final String symbol;
  final String name;
  final int height;
  final String plateName;
  final int floatMarketVal;

  TiantiStock({
    required this.code,
    required this.symbol,
    required this.name,
    required this.height,
    required this.plateName,
    required this.floatMarketVal,
  });

  factory TiantiStock.fromJson(Map<String, dynamic> json) {
    return TiantiStock(
      code: json['code'] ?? '',
      symbol: json['symbol'] ?? '',
      name: json['name'] ?? '',
      height: json['height'] ?? 1,
      plateName: json['plate_name'] ?? '',
      floatMarketVal: json['float_market_val'] ?? 0,
    );
  }
}

class PanmianEvent {
  final int timeSec;
  final String tagName;
  final int tagType; // 2=涨停, 0=跌停
  final String plateName;
  final String detail;

  PanmianEvent({
    required this.timeSec,
    required this.tagName,
    required this.tagType,
    required this.plateName,
    required this.detail,
  });

  factory PanmianEvent.fromJson(Map<String, dynamic> json) {
    return PanmianEvent(
      timeSec: json['time_sec'] ?? 0,
      tagName: json['tag_name'] ?? '',
      tagType: json['tag_type'] ?? 1,
      plateName: json['plate_name'] ?? '',
      detail: json['detail'] ?? '',
    );
  }
}

class TabCrowding {
  final String mainlineDesc;
  final List<MainlineChance> mainlines;
  final List<TuyereItem> tuyeres;
  final List<FundHeatItem> fundHeat;

  TabCrowding({
    required this.mainlineDesc,
    required this.mainlines,
    required this.tuyeres,
    required this.fundHeat,
  });

  factory TabCrowding.fromJson(Map<String, dynamic> json) {
    return TabCrowding(
      mainlineDesc: json['mainline_desc'] ?? '',
      mainlines: (json['mainlines'] as List? ?? [])
          .map((e) => MainlineChance.fromJson(e))
          .toList(),
      tuyeres: (json['tuyeres'] as List? ?? [])
          .map((e) => TuyereItem.fromJson(e))
          .toList(),
      fundHeat: (json['fund_heat'] as List? ?? [])
          .map((e) => FundHeatItem.fromJson(e))
          .toList(),
    );
  }
}

class MainlineChance {
  final String desc;
  final List<MainlinePlate> plates;

  MainlineChance({required this.desc, required this.plates});

  factory MainlineChance.fromJson(Map<String, dynamic> json) {
    return MainlineChance(
      desc: json['desc'] ?? '',
      plates: (json['plates'] as List? ?? [])
          .map((e) => MainlinePlate.fromJson(e))
          .toList(),
    );
  }
}

class MainlinePlate {
  final String plateName;
  final String faucetName;
  final String faucetReason;

  MainlinePlate({
    required this.plateName,
    required this.faucetName,
    required this.faucetReason,
  });

  factory MainlinePlate.fromJson(Map<String, dynamic> json) {
    return MainlinePlate(
      plateName: json['plate_name'] ?? '',
      faucetName: json['faucet_name'] ?? '',
      faucetReason: json['faucet_reason'] ?? '',
    );
  }
}

class TuyereItem {
  final String title;
  final String interpret;

  TuyereItem({required this.title, required this.interpret});

  factory TuyereItem.fromJson(Map<String, dynamic> json) {
    return TuyereItem(
      title: json['title'] ?? '',
      interpret: json['interpret'] ?? '',
    );
  }
}

class FundHeatItem {
  final String plateName;
  final String change;
  final String inflowStr;

  FundHeatItem({
    required this.plateName,
    required this.change,
    required this.inflowStr,
  });

  factory FundHeatItem.fromJson(Map<String, dynamic> json) {
    return FundHeatItem(
      plateName: json['plate_name'] ?? '',
      change: json['change'] ?? '',
      inflowStr: json['inflow_str'] ?? '',
    );
  }
}

class FocusStock {
  final String name;
  final String code;
  final String sector;
  final String poolType;
  final String entryRule;
  final String defenseRule;
  final String status; // 'green', 'yellow', 'red'
  final bool isCustom;
  final int height;
  final double latestClose;
  final List<Map<String, dynamic>> candles;
  final List<Map<String, dynamic>> ema5;
  final List<Map<String, dynamic>> ema20;

  FocusStock({
    required this.name,
    required this.code,
    required this.sector,
    required this.poolType,
    required this.entryRule,
    required this.defenseRule,
    required this.status,
    required this.isCustom,
    required this.height,
    this.latestClose = 0.0,
    this.candles = const [],
    this.ema5 = const [],
    this.ema20 = const [],
  });

  factory FocusStock.fromJson(Map<String, dynamic> json) {
    return FocusStock(
      name: json['name'] ?? '',
      code: json['code'] ?? '',
      sector: json['sector'] ?? '',
      poolType: json['pool_type'] ?? '',
      entryRule: json['entry_rule'] ?? '',
      defenseRule: json['defense_rule'] ?? '',
      status: json['status'] ?? 'yellow',
      isCustom: json['is_custom'] ?? false,
      height: json['height'] ?? 1,
      latestClose: (json['latest_close'] as num? ?? 0.0).toDouble(),
      candles: (json['candles'] as List? ?? []).map((e) => Map<String, dynamic>.from(e)).toList(),
      ema5: (json['ema5'] as List? ?? []).map((e) => Map<String, dynamic>.from(e)).toList(),
      ema20: (json['ema20'] as List? ?? []).map((e) => Map<String, dynamic>.from(e)).toList(),
    );
  }
}

class StrategyPlan {
  final String title;
  final String actionSlogan;
  final String recommendedPosition;
  final List<String> rules;
  final String defenseLine;

  StrategyPlan({
    required this.title,
    required this.actionSlogan,
    required this.recommendedPosition,
    required this.rules,
    required this.defenseLine,
  });

  factory StrategyPlan.fromJson(Map<String, dynamic> json) {
    return StrategyPlan(
      title: json['title'] ?? '',
      actionSlogan: json['action_slogan'] ?? '',
      recommendedPosition: json['recommended_position'] ?? '',
      rules: (json['rules'] as List? ?? []).map((e) => e.toString()).toList(),
      defenseLine: json['defense_line'] ?? '',
    );
  }
}
