"""Background jobs running the AI analysis of a binary.

The analysis takes minutes, so it runs in a thread of the web server rather
than inside the HTTP request that starts it: leaving or reloading the page does
not interrupt it, and does not start it again. Its progress is saved in the
binary's ``AIAnalysis`` row, which the page polls.
"""
import threading
from logging import getLogger

from django.db import connection, transaction
from django.utils import timezone

from . import ai
from .models import AIAnalysis

logger = getLogger('explorer.ai')


def start(binary, config, decompilations, from_interpretation=False):
    """Start the analysis of ``binary`` unless it is already running.

    ``decompilations`` is a list of ``(name, version, text)`` tuples, unused
    when ``from_interpretation`` re-runs only the interpretation of the saved
    merge. Returns ``(analysis, started)``.
    """
    with transaction.atomic():
        AIAnalysis.objects.get_or_create(binary=binary, defaults={'status': AIAnalysis.FAILED})
        # Lock the row so that two requests cannot both start a job.
        analysis = AIAnalysis.objects.select_for_update().get(binary=binary)
        if analysis.running:
            return analysis, False

        analysis.error = ''
        analysis.step_started = timezone.now()
        if from_interpretation:
            analysis.status = AIAnalysis.INTERPRETING
        else:
            analysis.status = AIAnalysis.MERGING
            analysis.merged = analysis.merge_model = ''
            analysis.merge_time = None
            analysis.decompilers = [name for name, _, _ in decompilations]
        analysis.interpreted = analysis.interpret_model = ''
        analysis.interpret_time = None
        analysis.save()

    thread = threading.Thread(target=_run, name=f'ai-analysis-{binary.pk}', daemon=True,
                              args=(binary.pk, config, decompilations, from_interpretation))
    thread.start()
    return analysis, True


def _error_message(exc):
    if isinstance(exc, ai.AIRateLimitError) and exc.retry_after:
        return f"{exc} Retry after {exc.retry_after}s."
    return str(exc) or exc.__class__.__name__


def _run(binary_id, config, decompilations, from_interpretation):
    rows = AIAnalysis.objects.filter(pk=binary_id)
    try:
        if not from_interpretation:
            merge = ai.generate_merge(config, decompilations)
            rows.update(merged=merge['best'], merge_model=merge['model'], merge_time=merge['seconds'],
                        status=AIAnalysis.INTERPRETING, step_started=timezone.now(),
                        updated=timezone.now())
            merged = merge['best']
        else:
            merged = rows.values_list('merged', flat=True).first() or ''

        interpretation = ai.generate_interpretation(config, merged)
        rows.update(interpreted=interpretation['interpreted'], interpret_model=interpretation['model'],
                    interpret_time=interpretation['seconds'], status=AIAnalysis.DONE,
                    step_started=None, updated=timezone.now())
    except Exception as exc:
        logger.error("AI analysis of %s failed: %s", binary_id, exc)
        rows.update(status=AIAnalysis.FAILED, error=_error_message(exc), step_started=None,
                    updated=timezone.now())
    finally:
        # This thread has its own database connection.
        connection.close()
