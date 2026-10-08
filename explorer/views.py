import datetime
import time
from logging import getLogger

from django.conf import settings
from django.db import transaction
from django.forms import model_to_dict
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page

from rest_framework import viewsets, permissions, mixins
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.renderers import TemplateHTMLRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from . import ai, ai_jobs
from .models import AIAnalysis, Binary, Decompilation, DecompilationRequest, Decompiler, \
    rerun_binary_decompilation
from .serializers import DecompilationRequestSerializer, DecompilationSerializer, BinarySerializer, \
    DecompilerSerializer

from .permissions import IsWorkerOrAdmin, ReadOnly

logger = getLogger('django')

class DecompilationRequestViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = DecompilationRequestSerializer
    permission_classes = [IsWorkerOrAdmin]
    queryset = DecompilationRequest.objects.select_related("binary", "decompiler")

    @action(methods=["POST"], detail=False)
    def claim(self, request):
        decompiler_id = request.data.get("decompiler")
        if not decompiler_id:
            return Response(
                {"decompiler": ["This field is required."]},
                status=400,
            )

        now = timezone.now()
        retry_cutoff = now - datetime.timedelta(seconds=300)

        with transaction.atomic():
            earliest_req = (
                DecompilationRequest.objects
                .select_for_update(skip_locked=True) # If another worker is already claiming this then move on
                .select_related("binary", "decompiler")
                .filter(decompiler_id=decompiler_id, last_attempted__lt=retry_cutoff)
                .order_by("created")
                .first()
            )

            if earliest_req is None:
                return Response(status=204)

            earliest_req.last_attempted = now
            earliest_req.save(update_fields=["last_attempted"])

        logger.debug(
            "Giving request %s to %s",
            earliest_req,
            request.META.get("REMOTE_ADDR"),
        )
        return Response(self.get_serializer(earliest_req).data)


    @action(methods=['POST'], detail=True)
    def complete(self, request, pk=None):
        serializer = DecompilationSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            instance = self.get_object()
            try:
                # Make completion idempotent: a Decompilation may already exist
                # for this (binary, decompiler) pair (e.g. a re-run, or a request
                # re-queued after a previous result). Update it in place instead
                # of inserting a duplicate (which violates the unique constraint
                # and 500s). This also repopulates a result whose file went
                # missing.
                existing = Decompilation.objects.filter(
                    binary=instance.binary, decompiler=instance.decompiler
                ).first()
                if existing is not None:
                    serializer.instance = existing
                serializer.save(binary=instance.binary, decompiler=instance.decompiler)
                return Response(serializer.data)
            finally:
                instance.delete()
        else:
            return Response(serializer.errors, status=400)

class DecompilerViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = DecompilerSerializer
    queryset = Decompiler.objects.all()
    permission_classes = [IsWorkerOrAdmin|ReadOnly]

    @action(methods=['GET'], detail=True)
    def health_check(self, *args, **kwargs):
        instance = self.get_object()
        instance.last_health_check = timezone.now()
        instance.save(update_fields=['last_health_check'])
        return Response()

    def perform_create(self, serializer):
        name = serializer.validated_data['name']
        # Request featured status of previous version of this
        latest = None
        for decompiler in Decompiler.objects.filter(name=name):
            if latest is None or latest < decompiler:
                latest = decompiler

        featured = False
        if latest is not None and latest.featured:
            featured = True

        serializer.save(featured=featured)


class BinaryViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = Binary.objects.all()
    serializer_class = BinarySerializer

    def get_permissions(self):
        if self.action == 'create':
            permission_classes = [AllowAny]
        else:
            permission_classes = [IsWorkerOrAdmin]
        return [permission() for permission in permission_classes]

    def perform_create(self, serializer):
        instance = serializer.save()
        for decompiler in Decompiler.healthy_latest_versions().values():
            _ = DecompilationRequest.objects.get_or_create(binary=instance, decompiler=decompiler)

    @action(methods=['GET'], detail=True)
    def download(self, *args, **kwargs):
        instance = self.get_object()
        try:
            handle = instance.file.open()
            size = instance.file.size
        except FileNotFoundError:
            raise Http404("Binary file is no longer available on this server.")

        response = FileResponse(handle, content_type='application/octet-stream')
        response['Content-Length'] = size
        response['Content-Disposition'] = f'attachment; filename="{instance.file.name}"'
        return response

    @action(methods=['POST'], detail=True)
    def rerun_all(self, *args, **kwargs):
        instance = self.get_object()
        # Create requests for all healthy decomps

        # TODO: Whenever multi-version is ready, use all or something?
        for decompiler in Decompiler.healthy_latest_versions().values():
            try:
                rerun_binary_decompilation(instance, decompiler)
            except ValueError:
                pass
        return Response()


class DecompilationViewSet(mixins.RetrieveModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    queryset = Decompilation.objects.none()
    serializer_class = DecompilationSerializer

    def get_permissions(self):
        if self.action in ['retrieve', 'list', 'download', 'rerun', 'ai_analysis']:
            permission_classes = [AllowAny]
        else:
            permission_classes = [IsAdminUser]
        return [permission() for permission in permission_classes]

    def get_queryset(self):
        binary = self.get_binary()
        queryset = Decompilation.objects.filter(binary=binary).select_related("binary", "decompiler")
        return queryset

    @action(methods=['GET'], detail=True)
    def download(self, *args, **kwargs):
        instance = self.get_object()

        try:
            handle = instance.decompiled_file.open()
            size = instance.decompiled_file.size
        except FileNotFoundError:
            raise Http404("Decompilation file is no longer available on this server.")
        filename = instance.decompiled_file.name.split('/')[-1]

        response = FileResponse(handle, content_type='application/octet-stream')
        response['Content-Length'] = size
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    @action(methods=['POST'], detail=True)
    def rerun(self, *args, **kwargs):
        instance = self.get_object()

        # TODO: Whenever multi-version is ready, use the one they request
        new_decompiler = Decompiler.healthy_latest_versions().get(instance.decompiler.name, None)
        if new_decompiler is None:
            return Response({
                "error": "Not re-running decompliation for decompiler with no active runners."
            }, status=400)

        rerun_binary_decompilation(instance.binary, new_decompiler)
        return Response()

    @action(methods=['GET', 'POST'], detail=False)
    def ai_analysis(self, request, *args, **kwargs):
        """GET returns the AI analysis of this binary, finished or in progress
        (``status``: merging, interpreting, done or failed). POST starts it as a
        background job, unless it is already running: a faithful merge of all
        successful decompiler outputs (``best``), then an AI interpretation of
        that merge (``interpreted``).

        ``step: interpret`` re-runs only the interpretation of the saved merge.
        ``provider``, ``api_key``, ``model`` and ``base_url`` use the browser's
        own AI provider instead of the server's."""
        binary = self.get_binary()
        analysis = AIAnalysis.objects.filter(binary=binary).first()
        if request.method == 'GET':
            if analysis is None:
                return Response({"error": "This binary has not been analysed yet."}, status=404)
            return Response(analysis.as_dict())

        if analysis is not None and analysis.running:
            return Response(analysis.as_dict())

        # "merge" and "all" both run the whole analysis (former API).
        step = request.data.get('step', 'all')
        if step not in ('all', 'merge', 'interpret'):
            return Response({"error": f"Unknown step {step!r}."}, status=400)
        config, error = _ai_config(request)
        if error:
            return error

        decompilations = []
        if step == 'interpret':
            if analysis is None or not analysis.merged:
                return Response({"error": "Run the merge first."}, status=409)
        else:
            decompilations = self._ai_decompilations()
            if not decompilations:
                return Response(
                    {"error": "No successful decompilations are available yet for "
                              "this binary. Wait for the decompilers to finish."},
                    status=409,
                )

        analysis, started = ai_jobs.start(binary, config, decompilations,
                                          from_interpretation=step == 'interpret')
        return Response(analysis.as_dict(), status=202 if started else 200)

    def _ai_decompilations(self):
        """Return ``(name, version, text)`` for the latest successful output of
        each decompiler on this binary."""
        best_per_decompiler = {}
        for decomp in self.get_queryset():
            if decomp.failed or decomp.decompiler is None:
                continue
            name = decomp.decompiler.name
            current = best_per_decompiler.get(name)
            if current is None or current.decompiler < decomp.decompiler:
                best_per_decompiler[name] = decomp

        decompilations = []
        for name, decomp in sorted(best_per_decompiler.items()):
            try:
                text = ai.read_decompilation_text(decomp)
            except Exception as exc:  # pragma: no cover - storage issues
                logger.warning("Could not read decompilation %s: %s", decomp.id, exc)
                continue
            decompilations.append((name, decomp.decompiler.version, text))
        return decompilations

    def get_binary(self):
        binary_id = self.kwargs.get('binary_id')
        return get_object_or_404(Binary, id=binary_id)


class IndexView(APIView):
    renderer_classes = [TemplateHTMLRenderer]
    template_name = 'explorer/index.html'
    permission_classes = [permissions.AllowAny]

    @method_decorator(cache_page(5))
    def get(self, request):
        # TODO: Whenever multi-version is ready, show em all
        decompilers = sorted(Decompiler.healthy_latest_versions().values(), key=lambda d: d.name.lower())

        decompilers_json = {}
        for d in decompilers:
            decompilers_json[d.name] = model_to_dict(d)

        return Response({
            'serializer': BinarySerializer(),
            'decompilers': decompilers,
            'decompilers_json': decompilers_json,
        })


def _ai_config(request):
    """Return ``(config, None)``, or ``(None, error response)``."""
    try:
        config = ai.config_from_request(request.data)
    except ValueError as exc:
        return None, Response({"error": str(exc)}, status=400)
    if config is None:
        return None, Response(
            {"error": "No AI provider is configured: add your API key in Settings."},
            status=503,
        )
    return config, None


def _ai_error_response(exc):
    if isinstance(exc, ai.AIRateLimitError):
        hint = str(exc)
        if exc.retry_after:
            hint += f" Retry after {exc.retry_after}s."
        logger.warning("AI request rate limited: %s", exc)
        resp = Response({"error": hint}, status=429)
        if exc.retry_after:
            resp["Retry-After"] = str(exc.retry_after)
        return resp
    logger.error("AI request failed: %s", exc)
    return Response({"error": f"AI request failed: {exc}"}, status=502)


class AITestView(APIView):
    """Check an AI provider config (the browser's, or the server's) with a tiny
    prompt."""
    renderer_classes = [JSONRenderer]
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        config, error = _ai_config(request)
        if error:
            return error
        started = time.monotonic()
        try:
            model = ai.test_connection(config)
        except Exception as exc:
            return _ai_error_response(exc)
        return Response({'provider': config.label, 'model': model,
                         'seconds': time.monotonic() - started})


def _history_item(binary):
    decompilations = list(binary.decompilations.all())
    failed = sum(1 for d in decompilations if d.failed)
    try:
        size = binary.file.size
    except (OSError, ValueError):
        size = None
    analysis = getattr(binary, 'ai_analysis', None)
    state = analysis.as_dict() if analysis is not None else None
    return {
        'id': binary.id,
        'name': binary.name,
        'created': binary.created,
        'size': size,
        'decompiled': len(decompilations) - failed,
        'failed': failed,
        'analysis': None if state is None else {
            'status': state['status'],
            'interpreted': bool(state['interpreted']),
            'merged': bool(state['best']),
            'model': state['model'],
            'updated': state['updated'],
        },
    }


class HistoryView(APIView):
    """The analysed binaries, newest first (GET), one of them (GET with an id),
    or remove one with its decompilations and analysis (DELETE)."""
    renderer_classes = [JSONRenderer]
    permission_classes = [permissions.AllowAny]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not settings.HISTORY_ENABLED:
            raise Http404("The history is disabled on this server.")

    def get(self, request, binary_id=None):
        queryset = Binary.objects.prefetch_related('decompilations').select_related('ai_analysis')
        if binary_id is not None:
            return Response(_history_item(get_object_or_404(queryset, id=binary_id)))
        try:
            limit = max(1, min(int(request.query_params.get('limit', 100)), 1000))
        except ValueError:
            limit = 100
        binaries = queryset.order_by('-created')[:limit]
        return Response({
            'decompilers': len(Decompiler.healthy_latest_versions()),
            'results': [_history_item(b) for b in binaries],
        })

    def delete(self, request, binary_id=None):
        binary = get_object_or_404(Binary, id=binary_id)
        files = [(binary.file.storage, binary.file.name)] if binary.file else []
        outputs = [(d.decompiled_file.storage, d.decompiled_file.name)
                   for d in binary.decompilations.all() if d.decompiled_file]
        binary.delete()
        # Outputs are stored by content hash, so another binary may share one.
        files += [(storage, name) for storage, name in outputs
                  if not Decompilation.objects.filter(decompiled_file=name).exists()]
        for storage, name in files:
            try:
                storage.delete(name)
            except Exception as exc:  # pragma: no cover - storage issues
                logger.warning("Could not delete %s: %s", name, exc)
        return Response(status=204)


class QueueView(APIView):
    renderer_classes = [JSONRenderer, TemplateHTMLRenderer]
    permission_classes = [permissions.AllowAny]
    template_name = 'explorer/queue.html'

    def get(self, request):
        if request.accepted_renderer.format == 'html':
            return Response({})
        else:
            return Response(DecompilationRequest.get_queue())
