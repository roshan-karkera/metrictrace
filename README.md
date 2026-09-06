# MetricTrace

A small analytics platform run the way a real one has to be run: pipelines that
know when they cannot be trusted, metrics that have one agreed definition, and a
change and incident process that decides what happens when either breaks.

## Run

    pip install -r requirements.txt
    python -m ingest.smard --weeks 8
    python -m models.clean
    python -m models.dimensional
    streamlit run app/dashboard.py

## Layout

    ingest/         extraction, lands raw immutably
    models/         raw to cleaned to dimensional, plus lineage
    semantic/       metric definitions, the single source of truth
    quality/        checks and the gate that blocks a bad load
    agent/          the why-did-it-move agent and its evaluation
    app/            dashboard
    process/        runbooks, incidents, tickets, BPMN models
    orchestration/  Airflow, added last on purpose
    docs/decisions/ architecture decision records

## Status

Portfolio project. Not production, no real users.
