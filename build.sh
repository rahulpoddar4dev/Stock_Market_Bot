#!/usr/bin/env bash
# Exit immediately if a command exits with a non-zero status
set -o errexit

echo "==> Upgrading pip, setuptools (<70) and wheel to prevent FakeBdist build isolation errors..."
pip install --upgrade pip "setuptools<70.0.0" wheel

echo "==> Installing dependencies with --no-build-isolation..."
pip install --no-build-isolation -r requirements.txt

echo "==> Build completed successfully!"
