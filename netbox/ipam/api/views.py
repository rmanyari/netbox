from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404
from django_pglocks import advisory_lock
from drf_spectacular.utils import extend_schema
from netaddr import IPSet
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.routers import APIRootView
from rest_framework.views import APIView

from circuits.models import Provider
from dcim.models import Site
from ipam import filtersets
from ipam.models import *
from ipam.models import L2VPN, L2VPNTermination
from ipam.utils import get_next_available_prefix
from netbox.api.viewsets import NetBoxModelViewSet
from netbox.api.viewsets.mixins import ObjectValidationMixin
from netbox.config import get_config
from netbox.constants import ADVISORY_LOCK_KEYS
from utilities.api import get_serializer_for_model
from utilities.utils import count_related
from . import serializers


class IPAMRootView(APIRootView):
    """
    IPAM API root view
    """
    def get_view_name(self):
        return 'IPAM'


#
# Viewsets
#

class ASNRangeViewSet(NetBoxModelViewSet):
    queryset = ASNRange.objects.prefetch_related('tenant', 'rir').all()
    serializer_class = serializers.ASNRangeSerializer
    filterset_class = filtersets.ASNRangeFilterSet


class ASNViewSet(NetBoxModelViewSet):
    queryset = ASN.objects.prefetch_related('tenant', 'rir').annotate(
        site_count=count_related(Site, 'asns'),
        provider_count=count_related(Provider, 'asns')
    )
    serializer_class = serializers.ASNSerializer
    filterset_class = filtersets.ASNFilterSet


class VRFViewSet(NetBoxModelViewSet):
    queryset = VRF.objects.prefetch_related('tenant').prefetch_related(
        'import_targets', 'export_targets', 'tags'
    ).annotate(
        ipaddress_count=count_related(IPAddress, 'vrf'),
        prefix_count=count_related(Prefix, 'vrf')
    )
    serializer_class = serializers.VRFSerializer
    filterset_class = filtersets.VRFFilterSet


class RouteTargetViewSet(NetBoxModelViewSet):
    queryset = RouteTarget.objects.prefetch_related('tenant').prefetch_related('tags')
    serializer_class = serializers.RouteTargetSerializer
    filterset_class = filtersets.RouteTargetFilterSet


class RIRViewSet(NetBoxModelViewSet):
    queryset = RIR.objects.annotate(
        aggregate_count=count_related(Aggregate, 'rir')
    ).prefetch_related('tags')
    serializer_class = serializers.RIRSerializer
    filterset_class = filtersets.RIRFilterSet


class AggregateViewSet(NetBoxModelViewSet):
    queryset = Aggregate.objects.prefetch_related('rir').prefetch_related('tags')
    serializer_class = serializers.AggregateSerializer
    filterset_class = filtersets.AggregateFilterSet


class RoleViewSet(NetBoxModelViewSet):
    queryset = Role.objects.annotate(
        prefix_count=count_related(Prefix, 'role'),
        vlan_count=count_related(VLAN, 'role')
    ).prefetch_related('tags')
    serializer_class = serializers.RoleSerializer
    filterset_class = filtersets.RoleFilterSet


class PrefixViewSet(NetBoxModelViewSet):
    queryset = Prefix.objects.prefetch_related(
        'site', 'vrf__tenant', 'tenant', 'vlan', 'role', 'tags'
    )
    serializer_class = serializers.PrefixSerializer
    filterset_class = filtersets.PrefixFilterSet

    parent_model = Prefix  # AvailableIPsMixin

    def get_serializer_class(self):
        if self.action == "available_prefixes" and self.request.method == "POST":
            return serializers.PrefixLengthSerializer
        return super().get_serializer_class()


class IPRangeViewSet(NetBoxModelViewSet):
    queryset = IPRange.objects.prefetch_related('vrf', 'role', 'tenant', 'tags')
    serializer_class = serializers.IPRangeSerializer
    filterset_class = filtersets.IPRangeFilterSet

    parent_model = IPRange  # AvailableIPsMixin


class IPAddressViewSet(NetBoxModelViewSet):
    queryset = IPAddress.objects.prefetch_related(
        'vrf__tenant', 'tenant', 'nat_inside', 'nat_outside', 'tags', 'assigned_object'
    )
    serializer_class = serializers.IPAddressSerializer
    filterset_class = filtersets.IPAddressFilterSet

    @advisory_lock(ADVISORY_LOCK_KEYS['available-ips'])
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @advisory_lock(ADVISORY_LOCK_KEYS['available-ips'])
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @advisory_lock(ADVISORY_LOCK_KEYS['available-ips'])
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)


class FHRPGroupViewSet(NetBoxModelViewSet):
    queryset = FHRPGroup.objects.prefetch_related('ip_addresses', 'tags')
    serializer_class = serializers.FHRPGroupSerializer
    filterset_class = filtersets.FHRPGroupFilterSet
    brief_prefetch_fields = ('ip_addresses',)


class FHRPGroupAssignmentViewSet(NetBoxModelViewSet):
    queryset = FHRPGroupAssignment.objects.prefetch_related('group', 'interface')
    serializer_class = serializers.FHRPGroupAssignmentSerializer
    filterset_class = filtersets.FHRPGroupAssignmentFilterSet


class VLANGroupViewSet(NetBoxModelViewSet):
    queryset = VLANGroup.objects.annotate_utilization().prefetch_related('tags')
    serializer_class = serializers.VLANGroupSerializer
    filterset_class = filtersets.VLANGroupFilterSet


class VLANViewSet(NetBoxModelViewSet):
    queryset = VLAN.objects.prefetch_related(
        'site', 'group', 'tenant', 'role', 'tags'
    ).annotate(
        prefix_count=count_related(Prefix, 'vlan')
    )
    serializer_class = serializers.VLANSerializer
    filterset_class = filtersets.VLANFilterSet


class ServiceTemplateViewSet(NetBoxModelViewSet):
    queryset = ServiceTemplate.objects.prefetch_related('tags')
    serializer_class = serializers.ServiceTemplateSerializer
    filterset_class = filtersets.ServiceTemplateFilterSet


class ServiceViewSet(NetBoxModelViewSet):
    queryset = Service.objects.prefetch_related(
        'device', 'virtual_machine', 'tags', 'ipaddresses'
    )
    serializer_class = serializers.ServiceSerializer
    filterset_class = filtersets.ServiceFilterSet


class L2VPNViewSet(NetBoxModelViewSet):
    queryset = L2VPN.objects.prefetch_related('import_targets', 'export_targets', 'tenant', 'tags')
    serializer_class = serializers.L2VPNSerializer
    filterset_class = filtersets.L2VPNFilterSet


class L2VPNTerminationViewSet(NetBoxModelViewSet):
    queryset = L2VPNTermination.objects.prefetch_related('assigned_object')
    serializer_class = serializers.L2VPNTerminationSerializer
    filterset_class = filtersets.L2VPNTerminationFilterSet


#
# Views
#

def get_results_limit(request):
    """
    Return the lesser of the specified limit (if any) and the configured MAX_PAGE_SIZE.
    """
    config = get_config()
    try:
        limit = int(request.query_params.get('limit', config.PAGINATE_COUNT)) or config.MAX_PAGE_SIZE
    except ValueError:
        limit = config.PAGINATE_COUNT
    if config.MAX_PAGE_SIZE:
        limit = min(limit, config.MAX_PAGE_SIZE)

    return limit


class AvailableObjectsView(ObjectValidationMixin, APIView):
    """
    Return a list of dicts representing child objects that have not yet been created for a parent object.
    """
    read_serializer_class = None
    write_serializer_class = None
    advisory_lock_key = None

    def get_parent(self, request, pk):
        """
        Return the parent object.
        """
        raise NotImplemented()

    def get_available_objects(self, parent, limit=None):
        """
        Return all available objects for the parent.
        """
        raise NotImplemented()

    def get_extra_context(self, parent):
        """
        Return any extra context data for the serializer.
        """
        return {}

    def check_sufficient_available(self, requested_objects, available_objects):
        """
        Check if there exist a sufficient number of available objects to satisfy the request.
        """
        return len(requested_objects) <= len(available_objects)

    def prep_object_data(self, requested_objects, available_objects, parent):
        """
        Prepare data by setting any programmatically determined object attributes (e.g. next available VLAN ID)
        on the request data.
        """
        return requested_objects

    def get(self, request, pk):
        parent = self.get_parent(request, pk)
        limit = get_results_limit(request)
        available_objects = self.get_available_objects(parent, limit)

        serializer = self.read_serializer_class(available_objects, many=True, context={
            'request': request,
            **self.get_extra_context(parent),
        })

        return Response(serializer.data)

    def post(self, request, pk):
        self.queryset = self.queryset.restrict(request.user, 'add')
        parent = self.get_parent(request, pk)

        # Normalize request data to a list of objects
        requested_objects = request.data if isinstance(request.data, list) else [request.data]

        # Serialize and validate the request data
        serializer = self.write_serializer_class(data=requested_objects, many=True, context={
            'request': request,
            **self.get_extra_context(parent),
        })
        if not serializer.is_valid():
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST
            )

        with advisory_lock(ADVISORY_LOCK_KEYS[self.advisory_lock_key]):
            available_objects = self.get_available_objects(parent)

            # Determine if the requested number of objects is available
            if not self.check_sufficient_available(serializer.validated_data, available_objects):
                return Response(
                    {"detail": f"Insufficient resources are available to satisfy the request"},
                    status=status.HTTP_409_CONFLICT
                )

            # Prepare object data for deserialization
            requested_objects = self.prep_object_data(serializer.validated_data, available_objects, parent)

            # Initialize the serializer with a list or a single object depending on what was requested
            serializer_class = get_serializer_for_model(self.queryset.model)
            context = {'request': request}
            if isinstance(request.data, list):
                serializer = serializer_class(data=requested_objects, many=True, context=context)
            else:
                serializer = serializer_class(data=requested_objects[0], context=context)

            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            # Create the new IP address(es)
            try:
                with transaction.atomic():
                    created = serializer.save()
                    self._validate_objects(created)
            except ObjectDoesNotExist:
                raise PermissionDenied()

        return Response(serializer.data, status=status.HTTP_201_CREATED)


class AvailableASNsView(AvailableObjectsView):
    queryset = ASN.objects.all()
    read_serializer_class = serializers.AvailableASNSerializer
    write_serializer_class = serializers.AvailableASNSerializer
    advisory_lock_key = 'available-asns'

    def get_parent(self, request, pk):
        return get_object_or_404(ASNRange.objects.restrict(request.user), pk=pk)

    def get_available_objects(self, parent, limit=None):
        return parent.get_available_asns()[:limit]

    def get_extra_context(self, parent):
        return {
            'range': parent,
        }

    def prep_object_data(self, requested_objects, available_objects, parent):
        for i, request_data in enumerate(requested_objects):
            request_data.update({
                'rir': parent.rir.pk,
                'range': parent.pk,
                'asn': available_objects[i],
            })

        return requested_objects

    @extend_schema(methods=["get"], responses={200: serializers.AvailableASNSerializer(many=True)})
    def get(self, request, pk):
        return super().get(request, pk)

    @extend_schema(
        methods=["post"],
        responses={201: serializers.ASNSerializer(many=True)},
        request=serializers.ASNSerializer(many=True),
    )
    def post(self, request, pk):
        return super().post(request, pk)


class AvailablePrefixesView(AvailableObjectsView):
    queryset = Prefix.objects.all()
    read_serializer_class = serializers.AvailablePrefixSerializer
    write_serializer_class = serializers.PrefixLengthSerializer
    advisory_lock_key = 'available-prefixes'

    def get_parent(self, request, pk):
        return get_object_or_404(Prefix.objects.restrict(request.user), pk=pk)

    def get_available_objects(self, parent, limit=None):
        return parent.get_available_prefixes().iter_cidrs()

    def check_sufficient_available(self, requested_objects, available_objects):
        available_prefixes = IPSet(available_objects)
        for requested_object in requested_objects:
            if not get_next_available_prefix(available_prefixes, requested_object['prefix_length']):
                return False
        return True

    def get_extra_context(self, parent):
        return {
            'prefix': parent,
            'vrf': parent.vrf,
        }

    def prep_object_data(self, requested_objects, available_objects, parent):
        available_prefixes = IPSet(available_objects)
        for i, request_data in enumerate(requested_objects):

            # Find the first available prefix equal to or larger than the requested size
            if allocated_prefix := get_next_available_prefix(available_prefixes, request_data['prefix_length']):
                request_data.update({
                    'prefix': allocated_prefix,
                    'vrf': parent.vrf.pk if parent.vrf else None,
                })
            else:
                raise ValidationError("Insufficient space is available to accommodate the requested prefix size(s)")

        return requested_objects

    @extend_schema(methods=["get"], responses={200: serializers.AvailablePrefixSerializer(many=True)})
    def get(self, request, pk):
        return super().get(request, pk)

    @extend_schema(
        methods=["post"],
        responses={201: serializers.PrefixSerializer(many=True)},
        request=serializers.PrefixSerializer(many=True),
    )
    def post(self, request, pk):
        return super().post(request, pk)


class AvailableIPAddressesView(AvailableObjectsView):
    queryset = IPAddress.objects.all()
    read_serializer_class = serializers.AvailableIPSerializer
    write_serializer_class = serializers.AvailableIPSerializer
    advisory_lock_key = 'available-ips'

    def get_available_objects(self, parent, limit=None):
        # Calculate available IPs within the parent
        ip_list = []
        for index, ip in enumerate(parent.get_available_ips(), start=1):
            ip_list.append(ip)
            if index == limit:
                break
        return ip_list

    def get_extra_context(self, parent):
        return {
            'parent': parent,
            'vrf': parent.vrf,
        }

    def prep_object_data(self, requested_objects, available_objects, parent):
        available_ips = iter(available_objects)
        for i, request_data in enumerate(requested_objects):
            request_data.update({
                'address': f'{next(available_ips)}/{parent.mask_length}',
                'vrf': parent.vrf.pk if parent.vrf else None,
            })

        return requested_objects

    @extend_schema(methods=["get"], responses={200: serializers.AvailableIPSerializer(many=True)})
    def get(self, request, pk):
        return super().get(request, pk)

    @extend_schema(
        methods=["post"],
        responses={201: serializers.IPAddressSerializer(many=True)},
        request=serializers.IPAddressSerializer(many=True),
    )
    def post(self, request, pk):
        return super().post(request, pk)


class PrefixAvailableIPAddressesView(AvailableIPAddressesView):

    def get_parent(self, request, pk):
        return get_object_or_404(Prefix.objects.restrict(request.user), pk=pk)


class IPRangeAvailableIPAddressesView(AvailableIPAddressesView):

    def get_parent(self, request, pk):
        return get_object_or_404(IPRange.objects.restrict(request.user), pk=pk)


class AvailableVLANsView(AvailableObjectsView):
    queryset = VLAN.objects.all()
    read_serializer_class = serializers.AvailableVLANSerializer
    write_serializer_class = serializers.CreateAvailableVLANSerializer
    advisory_lock_key = 'available-vlans'

    def get_parent(self, request, pk):
        return get_object_or_404(VLANGroup.objects.restrict(request.user), pk=pk)

    def get_available_objects(self, parent, limit=None):
        return parent.get_available_vids()[:limit]

    def get_extra_context(self, parent):
        return {
            'group': parent,
        }

    def prep_object_data(self, requested_objects, available_objects, parent):
        for i, request_data in enumerate(requested_objects):
            request_data.update({
                'vid': available_objects.pop(0),
                'group': parent.pk,
            })

        return requested_objects

    @extend_schema(methods=["get"], responses={200: serializers.AvailableVLANSerializer(many=True)})
    def get(self, request, pk):
        return super().get(request, pk)

    @extend_schema(
        methods=["post"],
        responses={201: serializers.VLANSerializer(many=True)},
        request=serializers.VLANSerializer(many=True),
    )
    def post(self, request, pk):
        return super().post(request, pk)
