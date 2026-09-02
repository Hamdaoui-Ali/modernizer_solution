# Analysis Agent (AIMF)

## Description
L'Agent d'Analyse est le premier maillon de l'Agentic Migration Factory. Son rôle est de cartographier un projet Java/Spring Boot legacy sans jamais en modifier le code source. Il extrait les faits déterministes (POM, imports, tests) et utilise GitHub Copilot pour un enrichissement sémantique des risques.

## Artefacts générés (Contrats)
* `analysis_report.json` : Résumé déterministe de la stack et des efforts.
* `dependency_graph.json` : Arbre des dépendances Maven.
* `test_inventory.json` : Inventaire des tests existants et rapports Surefire.
* `config_inventory.json` : Capacités de l'application (DB, Sécurité).
* `analysis_summary.md` : Résumé lisible par un humain.

## Sécurité
L'agent fonctionne en mode **Read-Only** strict sur les dossiers sources et implémente un système de AllowList/DenyList pour ses écritures.