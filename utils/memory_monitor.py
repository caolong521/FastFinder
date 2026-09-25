"""Lightweight memory monitoring utilities for FastFinder."""

from __future__ import annotations

import gc
import os
import sys


def get_process_memory_mb() -> float:
    """Return current process RSS (Resident Set Size) in MB.
    
    Returns 0.0 on platforms where this information is unavailable.
    """
    try:
        # Windows-specific: use psutil if available
        try:
            import psutil
            process = psutil.Process(os.getpid())
            return process.memory_info().rss / (1024 * 1024)
        except ImportError:
            pass
        
        # Fallback: try reading from procfs (Linux/WSL)
        if sys.platform != "win32":
            try:
                with open(f"/proc/{os.getpid()}/status", "r") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            # Value is in kB
                            parts = line.split()
                            return int(parts[1]) / 1024
            except (FileNotFoundError, ValueError, IndexError):
                pass
        
        # Last resort: use resource module (Unix only)
        try:
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF)
            # ru_maxrss is in KB on Linux, bytes on macOS
            if sys.platform == "darwin":
                return usage.ru_maxrss / (1024 * 1024)
            else:
                return usage.ru_maxrss / 1024
        except (ImportError, AttributeError):
            pass
            
    except Exception:
        pass
    
    return 0.0


def force_gc() -> dict:
    """Force garbage collection and return statistics.
    
    Returns dict with 'collected', 'uncollectable', and 'freed_mb' keys.
    """
    before_mb = get_process_memory_mb()
    collected = gc.collect()
    after_mb = get_process_memory_mb()
    
    return {
        "collected": collected,
        "uncollectable": len(gc.garbage),
        "freed_mb": max(0.0, before_mb - after_mb),
        "before_mb": before_mb,
        "after_mb": after_mb,
    }


def get_object_counts() -> dict[str, int]:
    """Return counts of the most common object types in memory."""
    counts: dict[str, int] = {}
    for obj_type, count in gc.get_count():
        type_name = obj_type.__name__ if hasattr(obj_type, "__name__") else str(obj_type)
        counts[type_name] = count
    
    # Also check for common large containers
    all_objects = gc.get_objects()
    list_count = sum(1 for obj in all_objects if isinstance(obj, list))
    dict_count = sum(1 for obj in all_objects if isinstance(obj, dict))
    str_count = sum(1 for obj in all_objects if isinstance(obj, str))
    
    counts["list"] = list_count
    counts["dict"] = dict_count
    counts["str"] = str_count
    
    return counts


def format_memory_report() -> str:
    """Generate a human-readable memory usage report."""
    mem_mb = get_process_memory_mb()
    counts = get_object_counts()
    
    lines = [
        f"Memory Usage: {mem_mb:.1f} MB",
        f"Object Counts:",
    ]
    
    # Show top object types by count
    sorted_types = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10]
    for type_name, count in sorted_types:
        lines.append(f"  {type_name}: {count:,}")
    
    return "\n".join(lines)
