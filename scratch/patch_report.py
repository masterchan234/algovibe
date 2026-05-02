import re

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

def patch_report():
    path = "graphify-out/GRAPH_REPORT.md"
    with open(path, "r") as f:
        content = f.read()
    
    new_content = content
    for cid, name in COMMUNITY_NAMES.items():
        # Match Community X as a heading or in brackets
        new_content = re.sub(rf"Community {cid}", name, new_content)
    
    with open(path, "w") as f:
        f.write(new_content)
    print("✅ Patched GRAPH_REPORT.md with descriptive names.")

if __name__ == "__main__":
    patch_report()
