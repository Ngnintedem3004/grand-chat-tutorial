import json
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.db import transaction
from django.utils import timezone
from django.contrib.auth import authenticate, login, logout
from django.views.decorators.http import require_POST
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.generics import ListCreateAPIView
from .models import Room, Message
from .serializers import MessageSerializer


# ---------------- CSRF TOKEN ----------------

def get_csrf(request):
    """
    Returns a CSRF token to the client.
    """
    return JsonResponse({}, headers={'X-CSRFToken': get_token(request)})


# ---------------- LOGIN ----------------

@require_POST
def login_view(request):
    """
    Authenticates a user using username and password.
    """
    try:
        credentials = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'detail': 'Invalid JSON'}, status=400)

    username = credentials.get('username')
    password = credentials.get('password')

    if not username or not password:
        return JsonResponse({'detail': 'Provide username and password'}, status=400)

    user = authenticate(username=username, password=password)
    if not user:
        return JsonResponse({'detail': 'Invalid credentials'}, status=400)

    login(request, user)
    return JsonResponse({'user': {'id': user.pk, 'username': user.username}})


# ---------------- LOGOUT ----------------

@require_POST
def logout_view(request):
    """
    Logs out the current authenticated user.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'detail': 'Must be authenticated'}, status=403)

    logout(request)
    return JsonResponse({})


# ---------------- MESSAGE API ----------------

class MessageListCreateAPIView(ListCreateAPIView):
    """
    Handles listing and creating messages in a room.
    """
    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        room_id = self.kwargs['room_id']
        # Ensure the user is a member of the room
        from django.shortcuts import get_object_or_404
        from .models import RoomMember
        get_object_or_404(RoomMember, user=self.request.user, room_id=room_id)
        return Message.objects.filter(room_id=room_id).select_related('user', 'room').order_by('-created_at')

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        room_id = self.kwargs['room_id']
        room = Room.objects.select_for_update().get(id=room_id)

        # Optional: bump room version
        room.increment_version()

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Save message linked to room and user
        message = serializer.save(room=room, user=request.user)

        # Update last message and bumped timestamp
        room.last_message = message
        room.bumped_at = timezone.now()
        room.save()

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=201, headers=headers)
