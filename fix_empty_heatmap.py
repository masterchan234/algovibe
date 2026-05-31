import json

def generate_multi_replace():
    with open('/home/chan/projects/algovibe/dashboard/frontend/src/components/ProfitChart.vue', 'r') as f:
        content = f.read()

    target_content = """        <div class="asset-heatmap" v-if="(stats.asset_contributions && stats.asset_contributions.length > 0) || (store.positions && store.positions.length > 0)">
          <div class="heatmap-header">ACTIVE POSITIONS</div>
          <div class="heatmap-grid" style="margin-bottom: 12px;">
            <div 
              v-for="asset in activeContributions"
              :key="asset.pair"
              class="heatmap-tile"
              :style="tileStyle(asset.pnl)"
              :title="`${asset.trades} Trades | ${formatIdr(asset.pnl)}`"
              @click="onRadarClick(asset)"
            >
              <div class="tile-pair">{{ asset.pair.replace('USDT','').replace('IDR','') }}</div>
              <div class="tile-pnl">{{ formatIdr(asset.pnl) }}</div>
            </div>
          </div>
        </div>"""
    
    replacement_content = """        <div class="asset-heatmap">
          <div class="heatmap-header">ACTIVE POSITIONS</div>
          
          <div v-if="activeContributions && activeContributions.length > 0" class="heatmap-grid" style="margin-bottom: 12px;">
            <div 
              v-for="asset in activeContributions"
              :key="asset.pair"
              class="heatmap-tile"
              :style="tileStyle(asset.pnl)"
              :title="`${asset.trades} Trades | ${formatIdr(asset.pnl)}`"
              @click="onRadarClick(asset)"
            >
              <div class="tile-pair">{{ asset.pair.replace('USDT','').replace('IDR','') }}</div>
              <div class="tile-pnl">{{ formatIdr(asset.pnl) }}</div>
            </div>
          </div>
          
          <div v-else style="padding: 16px 0; text-align: center; color: #8b949e; font-size: 11px; font-style: italic;">
            Belum ada data koin (menunggu trade pertama)
          </div>
        </div>"""

    print(json.dumps([
        {
            "AllowMultiple": False,
            "TargetContent": target_content,
            "ReplacementContent": replacement_content,
            "StartLine": 150,
            "EndLine": 180
        }
    ]))

generate_multi_replace()
