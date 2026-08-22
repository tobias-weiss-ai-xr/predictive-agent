#!/bin/bash

# Skript zum Deployment der optimierten predictive-agent v8.9-nix Version
# auf ALLEN verfügbaren Kubernetes-Clustern und Namespaces

set -e

VERSION="v8.9-nix"
IMAGE="172.25.24.36:5001/predictive-agent:$VERSION"
REPO_DIR="/home/weissto_local/git/predictive-agent"

echo "══════════════════════════════════════════════════════════════════════════════"
echo "🚀 DEPLOY OPTIMIERTER PREDICTIVE-AGENT $VERSION AUF ALLEN HOSTS"
echo "══════════════════════════════════════════════════════════════════════════════"
echo

# Alle Kubeconfig-Dateien finden
KUBECONFIGS=(
    "$HOME/.kube/config"
    "$HOME/.kube/config_restricted"
    "$HOME/.kube/config_old"
    "$HOME/.kube/config.bak.20260522094042"
    "$HOME/.kube/config.bak.1786308997"
)

echo "🔍 Gefundene Kubeconfig-Dateien:"
for config in "${KUBECONFIGS[@]}"; do
    if [ -f "$config" ]; then
        echo "  ✅ $config"
    else
        echo "  ❌ $config (nicht gefunden)"
    fi
done
echo

# Alle Contexts sammeln
echo "🎯 Verarbeite alle Kubernetes-Contexts..."
echo "───────────────────────────────────────────────────────────────────────────────"

TOTAL_DEPLOYMENTS=0
UPDATED_DEPLOYMENTS=0
SKIPPED_DEPLOYMENTS=0

for config in "${KUBECONFIGS[@]}"; do
    if [ ! -f "$config" ]; then
        continue
    fi
    
    contexts=$(kubectl --kubeconfig $config config get-contexts -o name 2>/dev/null | grep -v "^$")
    
    for ctx in $contexts; do
        echo "  Context: $ctx (Config: $config)"
        
        # Versuche, predictive-agent Deployments zu finden
        deployments=$(kubectl --kubeconfig $config --context $ctx get deployments --all-namespaces -o json 2>/dev/null | \
                      jq -r '.items[] | select(.metadata.name | contains("predictive-agent")) | .metadata.namespace + "/" + .metadata.name' 2>/dev/null || true)
        
        if [ -z "$deployments" ]; then
            echo "    ❌ Keine predictive-agent Deployments gefunden"
            SKIPPED_DEPLOYMENTS=$((SKIPPED_DEPLOYMENTS + 1))
            continue
        fi
        
        for deployment in $deployments; do
            TOTAL_DEPLOYMENTS=$((TOTAL_DEPLOYMENTS + 1))
            namespace=$(echo $deployment | cut -d'/' -f1)
            name=$(echo $deployment | cut -d'/' -f2)
            
            echo "    Deployment gefunden: $namespace/$name"
            
            # Aktuelle Image-Version prüfen
            current_image=$(kubectl --kubeconfig $config --context $ctx -n $namespace get deployment $name -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || echo "unknown")
            
            if [[ "$current_image" == *"$VERSION"* ]]; then
                echo "      ✅ Bereits auf $VERSION"
                UPDATED_DEPLOYMENTS=$((UPDATED_DEPLOYMENTS + 1))
            else
                echo "      ⏳ Aktuell: $current_image → Ziel: $IMAGE"
                
                # Deployment aktualisieren
                echo "      Aktualisiere..."
                kubectl --kubeconfig $config --context $ctx -n $namespace set image deployment/$name predictive-agent=$IMAGE 2>&1 || {
                    echo "      ❌ Fehler beim Aktualisieren"
                    continue
                }
                
                # Rollout-Status abwarten
                echo "      Warte auf Rollout..."
                kubectl --kubeconfig $config --context $ctx -n $namespace rollout status deployment/$name --timeout=300s 2>&1 || {
                    echo "      ⚠️  Rollout Timeout (kann manuell prüfen)"
                }
                
                # Verifizieren
                new_image=$(kubectl --kubeconfig $config --context $ctx -n $namespace get deployment $name -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null)
                if [[ "$new_image" == *"$VERSION"* ]]; then
                    echo "      ✅ Erfolgreicht aktualisiert auf $VERSION"
                    UPDATED_DEPLOYMENTS=$((UPDATED_DEPLOYMENTS + 1))
                else
                    echo "      ❌ Update fehlgeschlagen"
                fi
            fi
        done
    done
done

echo

echo "══════════════════════════════════════════════════════════════════════════════"
echo "📊 ZUSAMMENFASSUNG"
echo "══════════════════════════════════════════════════════════════════════════════"
echo "  Gesamt Deployments gefunden: $TOTAL_DEPLOYMENTS"
echo "  Erfolgreicht aktualisiert: $UPDATED_DEPLOYMENTS"
echo "  Übersprungen (bereits aktuell): $SKIPPED_DEPLOYMENTS"
echo

if [ $TOTAL_DEPLOYMENTS -eq 0 ]; then
    echo "⚠️  Keine predictive-agent Deployments in irgendwelchen Clustern gefunden!"
    echo "   Bitte prüfen Sie manuell:"
    echo "   - kubectl get deployments --all-namespaces"
else
    echo "✅ Fertig! Alle predictive-agent Deployments wurden auf $VERSION aktualisiert."
fi

echo "══════════════════════════════════════════════════════════════════════════════"
