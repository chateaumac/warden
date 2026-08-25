"""Warden — keeps device settings the way you set them.

Audits ADB/SSH-reachable devices (Android TVs first) against declarative
profiles and re-applies anti-ad/anti-telemetry settings when firmware
updates silently revert them.
"""
