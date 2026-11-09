import json

def generate_multi_replace():
    with open('/home/chan/projects/algovibe/dashboard/frontend/src/components/ProfitChart.vue', 'r') as f:
        content = f.read()

    target_content = """        <div class="asset-heatmap" v-if="(stats.asset_contributions && stats.asset_contributions.length > 0) || (store.positions && store.positions.length > 0)">
          <div class="heatmap-header">ACTIVE POSITIONS</div>"""
    
    replacement_content = """        <div class="asset-heatmap">
          <div class="heatmap-header">ACTIVE POSITIONS</div>"""

    print(json.dumps([
        {
            "AllowMultiple": False,
            "TargetContent": target_content,
            "ReplacementContent": replacement_content,
            "StartLine": 158,
            "EndLine": 162
        }
    ]))

generate_multi_replace()
