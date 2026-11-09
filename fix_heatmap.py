import json

def generate_multi_replace():
    with open('/home/chan/projects/algovibe/dashboard/frontend/src/components/ProfitChart.vue', 'r') as f:
        content = f.read()

    target_content = """        <div class="asset-heatmap" v-if="stats.asset_contributions && stats.asset_contributions.length > 0">"""
    
    replacement_content = """        <div class="asset-heatmap" v-if="(stats.asset_contributions && stats.asset_contributions.length > 0) || (store.positions && store.positions.length > 0)">"""

    target_content2 = """const activeContributions = computed(() => {
  if (!stats.value?.asset_contributions) return [];
  const activePairs = store.positions.map(p => p.pair.toLowerCase());
  
  const activeMap = {};
  store.positions.forEach(p => {
    activeMap[p.pair.toLowerCase()] = { pair: p.pair, pnl: 0, trades: 0 };
  });

  stats.value.asset_contributions.forEach(a => {
    const p = a.pair.toLowerCase();
    if (activePairs.includes(p)) {
      activeMap[p] = a;
    }
  });

  return Object.values(activeMap);
});"""

    replacement_content2 = """const activeContributions = computed(() => {
  const activePairs = store.positions.map(p => p.pair.toLowerCase());
  
  const activeMap = {};
  store.positions.forEach(p => {
    activeMap[p.pair.toLowerCase()] = { pair: p.pair, pnl: Number(p.unrealized_pnl_quote || 0), trades: 0 };
  });

  if (stats.value?.asset_contributions) {
    stats.value.asset_contributions.forEach(a => {
      const p = a.pair.toLowerCase();
      if (activePairs.includes(p)) {
        activeMap[p] = {
          pair: a.pair,
          pnl: activeMap[p].pnl + a.pnl, // Realized + Unrealized
          trades: a.trades
        };
      }
    });
  }

  return Object.values(activeMap);
});"""

    print(json.dumps([
        {
            "AllowMultiple": False,
            "TargetContent": target_content,
            "ReplacementContent": replacement_content,
            "StartLine": 150,
            "EndLine": 170
        },
        {
            "AllowMultiple": False,
            "TargetContent": target_content2,
            "ReplacementContent": replacement_content2,
            "StartLine": 230,
            "EndLine": 260
        }
    ]))

generate_multi_replace()
