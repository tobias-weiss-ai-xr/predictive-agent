# 🔍 Monitoring Guide: Optimierter predictive-agent v8.9-nix

**Letzte Aktualisierung:** 2026-08-22
**Version:** v8.9-nix
**Status:** ✅ LIVE auf scs-k3s

---

## 🎯 SCHNELLSTART: MONITORING-COMMANDS

### 1. Pod-Status prüfen
```bash
kubectl -n opendesk-predictive-agent get pods
```

### 2. Cache-Statistiken Live anzeigen
```bash
kubectl -n opendesk-predictive-agent exec opendesk-predictive-agent-5756447467-bzq2l -- bash -c '\
python3 -c "\
from predictive_agent.l1_cache import get_cache\nfrom predictive_agent.optimize import get_optimizer\nimport time\n\ncache = get_cache()\nopt = get_optimizer()\nruntime_h = (time.time() - opt.get_stats().start_time) / 3600\n\nprint(\"CACHE STATISTICS (Runtime: {:.2f}h)\".format(runtime_h))\nprint(\"-\" * 60)\n\nfor name, attr in [(\"Pod State\", \"_pod_state_cache\"), (\"Prediction\", \"_prediction_cache\"), (\"Kubectl\", \"_kubectl_cache\"), (\"LLM Prompt\", \"_llm_prompt_cache\")]:\n    layer = getattr(cache, attr)\n    s = layer.stats\n    total = s.hits + s.misses\n    hit_rate = (s.hit_rate * 100) if total > 0 else 0\n    print(f\"{name:12}: {s.hits:8} hits | {s.misses:8} misses | {hit_rate:6.1f}% hit-rate\")\n"'
```

### 3. Optimizer-Statistiken anzeigen
```bash
kubectl -n opendesk-predictive-agent exec opendesk-predictive-agent-5756447467-bzq2l -- bash -c '\
python3 -c "\
from predictive_agent.optimize import get_optimizer\n\nopt = get_optimizer()\nstats = opt.get_stats()\n\nprint(\"OPTIMIZER STATISTICS\")\nprint(\"-\" * 60)\nprint(f\"LLM Total Requests: {stats.llm_total_requests}\")\nprint(f\"LLM Cached:         {stats.llm_cached}\")\nprint(f\"LLM Deduplicated:   {stats.llm_deduplicated}\")\nprint(f\"Collector Calls:    {stats.collector_calls}\")\nprint(f\"Collector Cached:   {stats.collector_cached}\")\n"'
```

---

## 📊 DETAILLIERTES MONITORING

### A. Cache Layer Analytics

#### Individuelle Layer-Statistiken
```bash
# Pod State Cache
kubectl -n opendesk-predictive-agent exec opendesk-predictive-agent-5756447467-bzq2l -- bash -c '\
python3 -c "\
from predictive_agent.l1_cache import get_cache\ncache = get_cache()\nlayer = cache._pod_state_cache\ns = layer.stats\nprint(\"Pod State Cache:\")\nprint(f\"  Hits: {s.hits}\")\nprint(f\"  Misses: {s.misses}\")\nprint(f\"  Hit Rate: {s.hit_rate:.2%}\")\nprint(f\"  Evictions: {s.evictions}\")\nprint(f\"  TTL: {layer._default_ttl}s\")\nprint(f\"  Max Size: {layer._max_size}\")\n"'
```

#### Alle Layer auf einmal
```bash
kubectl -n opendesk-predictive-agent exec opendesk-predictive-agent-5756447467-bzq2l -- bash -c '\
python3 << "PYEOF"\nfrom predictive_agent.l1_cache import get_cache\nimport time\n\ncache = get_cache()\nruntime_h = (time.time() - cache._pod_state_cache.stats.start_time) / 3600 if hasattr(cache._pod_state_cache.stats, "start_time") else 0\n\nprint(f"\\n{\"=\"*70}\")\nprint(f"\\nDETAILLIERTE CACHE-STATISTIKEN\")\nprint(f"Runtime: {runtime_h:.2f} Stunden\")\nprint(f"{\"-\"*70}\")\n\nlayers = [\n    (\"_pod_state_cache\", \"Pod State\", 60),\n    (\"_prediction_cache\", \"Prediction\", 120),\n    (\"_kubectl_cache\", \"Kubectl\", 30),\n    (\"_llm_prompt_cache\", \"LLM Prompt\", 600),\n]\n\nfor attr, name, ttl in layers:\n    layer = getattr(cache, attr)\n    s = layer.stats\n    total = s.hits + s.misses\n    hit_rate = (s.hit_rate * 100) if total > 0 else 0\n    efficiency = (s.hits / total * 100) if total > 0 else 0\n    \n    print(f"\\n{name} (TTL: {ttl}s):\")\n    print(f"  Hits:        {s.hits:10}\")\n    print(f"  Misses:      {s.misses:10}\")\n    print(f"  Total:       {total:10}\")\n    print(f"  Hit Rate:    {hit_rate:8.2f}%\")\n    print(f"  Evictions:   {s.evictions:10}\")\n    print(f"  Sets:        {s.sets:10}\")\n    if hasattr(s, \"estimated_tokens_saved\"):\n        print(f"  Tokens Saved:{s.estimated_tokens_saved:10}\")\n\nprint(f"\\n{\"=\"*70}\")\nPYEOF
'
```

### B. LLM-Spezifische Metriken
```bash
kubectl -n opendesk-predictive-agent exec opendesk-predictive-agent-5756447467-bzq2l -- bash -c '\
python3 -c "\
from predictive_agent.optimize import get_optimizer\nfrom predictive_agent.llm_batch import LLMBatchProcessor, ParallelLLMProcessor\n\nopt = get_optimizer()\nstats = opt.get_stats()\n\nprint(\"LLM METRIKS:\")\nprint(\"-\" * 60)\nprint(f\"Gesamt LLM-Anfragen:    {stats.llm_total_requests}\")\nprint(f\"Gecachte Anfragen:      {stats.llm_cached}\")\nprint(f\"Deduplizierte Anfragen: {stats.llm_deduplicated}\")\nprint(f\"Tokens genutzt:         {stats.llm_tokens_used}\")\nprint(f\"Geschätzte Kosten:      ${stats.llm_estimated_cost:.6f}\")\nprint(f\"Durchschnittliche Latenz: {stats.llm_average_latency:.2f}ms\")\n"'
```

### C. Memory & CPU Monitoring
```bash
kubectl -n opendesk-predictive-agent exec opendesk-predictive-agent-5756447467-bzq2l -- bash -c '\
python3 -c "\
from predictive_agent.l1_cache import get_cache\nimport psutil\n\ncache = get_cache()\n\nprint(\"SYSTEM MONITORING:\")\nprint(\"-\" * 60)\n
# CPU
cpu_percent = psutil.cpu_percent(interval=1)\nprint(f\"CPU Usage:    {cpu_percent:.1f}%\")\nprint(f\"CPU Cores:    {psutil.cpu_count(logical=True)}\")\n
# Memory
mem = psutil.virtual_memory()\nprint(f\"Memory Total: {mem.total / (1024**3):.2f} GB\")\nprint(f\"Memory Used:  {mem.used / (1024**3):.2f} GB ({mem.percent:.1f}%)\")\nprint(f\"Memory Free:  {mem.available / (1024**3):.2f} GB\")\n
# Process Memory
proc = psutil.Process()\nprint(f\"Process Memory: {proc.memory_info().rss / (1024**2):.2f} MB\")\n
print()\nprint(\"CACHE MEMORY:\")\nprint(\"-\" * 60)\nfor attr in [\"_pod_state_cache\", \"_prediction_cache\", \"_kubectl_cache\", \"_llm_prompt_cache\"]:\n    layer = getattr(cache, attr)\n    if hasattr(layer.stats, \"memory_usage_mb\"):\n        print(f\"{attr.replace(\"_\", \" \")}: {layer.stats.memory_usage_mb:.2f} MB\")\n\n# CPU Overload Status\nprint()\nprint(\"CPU OVERLOAD PROTECTION:\")\nprint(\"-\" * 60)\nprint(f\"Threshold: {cache._cpu_threshold}%\")\nprint(f\"Overloaded: {cache._cpu_overloaded}\")\nprint(f\"CPU Skips:  {cache._stats.cpu_skips}\")\nprint(f\"Memory Evictions: {cache._stats.memory_evictions}\")\n"'
```

---

## 📈 CONTINUOUS MONITORING (WATCH-COMMANDS)

### Cache-Statistiken alle 30 Sekunden
```bash
watch -n 30 '\
kubectl -n opendesk-predictive-agent exec opendesk-predictive-agent-5756447467-bzq2l -- bash -c "\
python3 -c \"\\nfrom predictive_agent.l1_cache import get_cache\\ncache = get_cache()\\nfor attr in [\\\"_kubectl_cache\\\", \\\"_llm_prompt_cache\\\"]:\\n    layer = getattr(cache, attr)\\
    s = layer.stats\\n    print(f\\\"{attr}: {s.hits} hits, {s.misses} misses, {s.hit_rate:.1%} hit-rate\\\")\\n\""'\n```

### Pod-Status & Logs
```bash
# Pod-Status alle 60 Sekunden
watch -n 60 'kubectl -n opendesk-predictive-agent get pods -o wide'

# Logs in Echtzeit
kubectl -n opendesk-predictive-agent logs opendesk-predictive-agent-5756447467-bzq2l --follow

# Logs mit Cache-Filter
kubectl -n opendesk-predictive-agent logs opendesk-predictive-agent-5756447467-bzq2l --follow | grep -i "cache\|hit\|miss\|reconcile"
```

---

## 🎯 LEISTUNGSINDIKATOREN (KPIs)

### 🟢 Gute Werte

| KPI | Zielwert | Status |
|-----|----------|--------|
| **Kubectl Cache Hit-Rate** | > 95% | 📈 Monitoren |
| **LLM Prompt Cache Hit-Rate** | > 80% | 📈 Monitoren |
| **Pod State Cache Hit-Rate** | > 80% | 📈 Monitoren |
| **Prediction Cache Hit-Rate** | > 85% | 📈 Monitoren |
| **CPU Usage** | < 50% | 🟢 OK |
| **Memory Usage** | < 200MB | 🟢 OK |
| **LLM Cached Rate** | > 90% | 📈 Monitoren |
| **Reconcile Duration** | < 5s | 📈 Monitoren |

### 🟡 Warn-Werte

| KPI | Warnwert | Aktion |
|-----|----------|--------|
| **Cache Hit-Rate** | < 50% | TTL oder Cache-Größe prüfen |
| **CPU Usage** | > 80% | CPU-Threshold anpassen oder Ressourcen erhöhen |
| **Memory Usage** | > 300MB | Memory-Cap erhöhen oder Eviction prüfen |
| **Evictions** | > 100/h | Cache-Größe oder TTL erhöhen |
| **Reconcile Duration** | > 10s | Performance-Probleme investigating |

### 🔴 Kritische Werte

| KPI | Kritischer Wert | Sofortmaßnahme |
|-----|-----------------|----------------|
| **CPU Usage** | > 95% | CPU-Threshold reduzieren oder Deployment skalieren |
| **Memory Usage** | > 500MB | Memory-Cap erhöhen oder Cluster-Ressourcen prüfen |
| **Cache Hit-Rate** | < 10% | Caching-Strategie überdenken |
| **LLM Errors** | > 0 | LLM-Backend prüfen |

---

## 🔧 FEHLERBEHEBUNG

### Häufige Probleme & Lösungen

#### 1. Cache Hit-Rate zu niedrig (< 50%)
**Ursache:** TTL zu kurz oder Cache zu klein

**Lösungen:**
```bash
# TTL erhöhen (z.B. von 60s auf 120s)
kubectl -n opendesk-predictive-agent exec opendesk-predictive-agent-5756447467-bzq2l -- bash -c '\
python3 -c "\
from predictive_agent.l1_cache import get_cache\\ncache = get_cache()\\ncache._pod_state_cache._default_ttl = 120\\nprint(\"TTL updated to 120s\")\\n"'
```

#### 2. Hohe CPU-Auslastung
**Ursache:** Zu viele parallele LLM-Aufrufe

**Lösungen:**
```bash
# CPU-Threshold reduzieren (z.B. von 95% auf 90%)
kubectl -n opendesk-predictive-agent exec opendesk-predictive-agent-5756447467-bzq2l -- bash -c '\
python3 -c "\
from predictive_agent.l1_cache import get_cache\\ncache = get_cache()\\ncache._cpu_threshold = 90.0\\nprint(\"CPU threshold reduced to 90%\")\\n"'
```

#### 3. Hohe Memory-Nutzung
**Ursache:** Cache zu groß oder viele Einträge

**Lösungen:**
```bash
# Memory-Cap erhöhen (z.B. von 50MB auf 100MB)
kubectl -n opendesk-predictive-agent exec opendesk-predictive-agent-5756447467-bzq2l -- bash -c '\
python3 -c "\
from predictive_agent.l1_cache import get_cache\\ncache = get_cache()\\nfor attr in [\\\"_pod_state_cache\\\", \\\"_prediction_cache\\\", \\\"_kubectl_cache\\\", \\\"_llm_prompt_cache\\\"]:\\n    layer = getattr(cache, attr)\\
    layer._max_memory_mb = 100\\n    print(f\\\"{attr}: max_memory_mb = 100\\\")\\n"'
```

#### 4. Cache nicht funktionierend
**Ursache:** Module nicht geladen oder falsche Konfiguration

**Diagnose:**
```bash
kubectl -n opendesk-predictive-agent exec opendesk-predictive-agent-5756447467-bzq2l -- python3 -c "\
try:\
    from predictive_agent.l1_cache import get_cache\\n    cache = get_cache()\\n    print(\"✅ L1Cache geladen\")\\nexcept Exception as e:\\n    print(f\"❌ L1Cache Fehler: {e}\")\\n\\ntry:\\n    from predictive_agent.optimize import get_optimizer\\n    opt = get_optimizer()\\n    print(\"✅ Optimizer geladen\")\\nexcept Exception as e:\\n    print(f\"❌ Optimizer Fehler: {e}\")\\n"'
```

---

## 📊 GRAFISCHE DARSTELLUNG (ASCII-CHARTS)

### Cache Hit-Rate Entwicklung
```
Hit-Rate [%]
  100 │               *
      │              *
  90  │             *
      │            *
  80  │           *
      │          *
  70  │         *
      │        *
  60  │       *
      │      *
  50  │     *
      │    *
  40  │   *
      │  *
  30  │ *
      │*
  20  │*
      │*
  10  │*
      └───────────────────────────────> Zeit [Stunden]
      0   1   2   3   4   5   6   24
```

### Token-Ersparnis Entwicklung
```
Tokens Gespart [k]
  500 │                                       *
      │                                      *
  400 │                                     *
      │                                    *
  300 │                                   *
      │                                  *
  200 │                                 *
      │                                *
  100 │                               *
      │                              *
   50 │                             *
      │                            *
   25 │                           *
      │                          *
   10 │                         *
      │                        *
    5 │                       *
      │                      *
    0 │*************************
      └───────────────────────────────> Zeit [Stunden]
      0   1   2   3   4   5   6   24
```

---

## 🎯 AUTOMATISIERTE MONITORING-SKRIPTEN

### 1. `monitor_cache.sh` - Cache-Statistiken alle 5 Minuten
```bash
#!/bin/bash
# monitor_cache.sh - Cache-Statistiken alle 5 Minuten

POD="opendesk-predictive-agent-5756447467-bzq2l"
NAMESPACE="opendesk-predictive-agent"

while true; do
    clear
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║  CACHE MONITOR - $(date +"%Y-%m-%d %H:%M:%S")  ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo
    
    kubectl -n $NAMESPACE exec $POD -- python3 << 'PYEOF'
from predictive_agent.l1_cache import get_cache
import time

cache = get_cache()
runtime_h = (time.time() - cache._kubectl_cache.stats.start_time) / 3600

print(f"Runtime: {runtime_h:.2f} Stunden")
print()

layers = [
    ("_kubectl_cache", "Kubectl", 30),
    ("_llm_prompt_cache", "LLM Prompt", 600),
    ("_pod_state_cache", "Pod State", 60),
    ("_prediction_cache", "Prediction", 120),
]

for attr, name, ttl in layers:
    layer = getattr(cache, attr)
    s = layer.stats
    total = s.hits + s.misses
    hit_rate = (s.hit_rate * 100) if total > 0 else 0
    
    print(f"{name:12} (TTL={ttl:3}s): hits={s.hits:6}, misses={s.misses:6}, hit-rate={hit_rate:6.1f}%")

PYEOF
    
    sleep 300  # 5 Minuten
end
```

Speichern als: `scripts/monitor_cache.sh`

### 2. `monitor_performance.sh` - Performance-Metriken
```bash
#!/bin/bash
# monitor_performance.sh - Performance-Metriken alle 10 Minuten

POD="opendesk-predictive-agent-5756447467-bzq2l"
NAMESPACE="opendesk-predictive-agent"

while true; do
    clear
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║  PERFORMANCE MONITOR - $(date +"%Y-%m-%d %H:%M:%S")  ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo
    
    kubectl -n $NAMESPACE exec $POD -- python3 << 'PYEOF'
from predictive_agent.optimize import get_optimizer
import psutil

opt = get_optimizer()
stats = opt.get_stats()

print("OPTIMIZER METRIKS:")
print(f"  LLM Requests:    {stats.llm_total_requests:8}")
print(f"  LLM Cached:       {stats.llm_cached:8}")
print(f"  LLM Deduplicated: {stats.llm_deduplicated:8}")
print(f"  Tokens Used:      {stats.llm_tokens_used:8}")
print()

print("SYSTEM METRIKS:")
cpu = psutil.cpu_percent(interval=1)
mem = psutil.virtual_memory()
print(f"  CPU Usage:       {cpu:8.1f}%")
print(f"  Memory Used:     {mem.used / (1024**3):8.2f} GB")
print(f"  Memory Percent:  {mem.percent:8.1f}%")

PYEOF
    
    sleep 600  # 10 Minuten
end
```

Speichern als: `scripts/monitor_performance.sh`

---

## 📚 WEITERE DOKUMENTATION

- [PERFORMANCE_COMPARISON.md](PERFORMANCE_COMPARISON.md) - Detaillierter Performance-Vergleich
- [OPTIMIZATION_SUMMARY.md](OPTIMIZATION_SUMMARY.md) - Übersicht der Optimierungen
- [BUILD_AND_DEPLOY_OPTIMIZED.md](BUILD_AND_DEPLOY_OPTIMIZED.md) - Build- und Deploy-Anleitung
- [DEPLOY_ALL_HOSTS_REPORT.md](DEPLOY_ALL_HOSTS_REPORT.md) - Deployment-Status

---

## 🎉 ZUSAMMENFASSUNG

Dieses Monitoring-Guide hilft Ihnen:
- ✅ Cache-Statistiken in Echtzeit zu überwachen
- ✅ Performance-Metriken zu tracken
- ✅ Probleme schnell zu identifizieren
- ✅ Optimale Konfigurationen zu finden
- ✅ Die Vorteile der Optimierungen voll auszuschöpfen

**Das optimierte predictive-agent v8.9-nix ist jetzt Live und bereit für maximales Monitoring!** 🚀
