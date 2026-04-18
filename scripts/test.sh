#!/bin/bash
set -e
echo "Running full test suite..."
pytest tests/ -v --tb=short
