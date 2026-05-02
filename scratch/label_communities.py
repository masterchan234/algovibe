import json
import re

# Mapping of Community IDs to Descriptive Names
COMMUNITY_NAMES = {
    0: "Oracle & Intelligence",
    1: "UI Frontend Logic",
    2: "Persistence & Database",
    3: "Dashboard Data Streams",
    4: "Execution & Bot Lifecycle",
    5: "Public API Endpoints",
    6: "ML & Technical Analysis",
    7: "Frontend Chart Helpers",
    8: "Private WS Streaming",
    9: "Deadman Switch Safety",
    10: "Exchange Integration (REST)",
    11: "Performance Auditing",
    12: "LLM Worker (LiteRT)",
    13: "Market Insights API",
    14: "Backtesting Engine",
    15: "Multi-tenant Provisioning",
    16: "SaaS Watchdog",
    17: "Mobile Pull-to-Refresh",
    18: "Scoring & Ranking",
    19: "System Reset Utilities",
    20: "Reset Dry Run Tool",
    21: "ML Training Engine",
    22: "Production Auditor",
    23: "Frontend Notifications",
    24: "Frontend Audio Effects",
    25: "V2 API Testing",
    26: "Global Configuration",
    27: "Package Metadata",
    28: "Package Metadata",
    29: "Package Metadata",
    30: "Package Metadata",
    31: "Package Metadata",
    32: "Package Metadata",
    33: "Frontend Build Config",
    34: "Frontend Trade Logic",
    35: "Package Metadata"
}

def patch_graph_json():
    path = "graphify-out/graph.json"
    with open(path, "r") as f:
        graph = json.load(f)
    
    # Add community_name to nodes
    for node in graph["nodes"]:
        cid = node.get("community")
        if cid in COMMUNITY_NAMES:
            node["community_name"] = COMMUNITY_NAMES[cid]
        else:
            node["community_name"] = f"Community {cid}"
            
    with open(path, "w") as f:
        json.dump(graph, f, indent=2)
    print("✅ Patched graph.json with community names.")

def patch_graph_html():
    path = "graphify-out/graph.html"
    with open(path, "r") as f:
        content = f.read()
    
    # Find the LEGEND array and replace labels
    def replace_legend(match):
        legend_json = match.group(1)
        legend = json.loads(legend_json)
        for item in legend:
            cid = item["cid"]
            if cid in COMMUNITY_NAMES:
                item["label"] = COMMUNITY_NAMES[cid]
        return f"const LEGEND = {json.dumps(legend)};"

    new_content = re.sub(r"const LEGEND = (\[.*?\]);", replace_legend, content)
    
    with open(path, "w") as f:
        f.write(new_content)
    print("✅ Patched graph.html with descriptive legend.")

if __name__ == "__main__":
    patch_graph_json()
    patch_graph_html()
