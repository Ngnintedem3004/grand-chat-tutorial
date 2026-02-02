from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import ListCreateAPIView
from .models import User, Room, Message, RoomMember


# ----------------- SERIALIZERS -----------------

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username']


class RoomSearchSerializer(serializers.ModelSerializer):
    is_member = serializers.SerializerMethodField()

    def get_is_member(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return request.user in obj.users.all()  # Assumes Room has a 'users' ManyToMany field
        return False

    class Meta:
        model = Room
        fields = ['id', 'name', 'created_at', 'updated_at', 'is_member']


class LastMessageSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = Message
        fields = ['id', 'content', 'user', 'created_at']


class RoomSerializer(serializers.ModelSerializer):
    member_count = serializers.SerializerMethodField()
    last_message = LastMessageSerializer(read_only=True)

    def get_member_count(self, obj):
        # If Room model has a member_count field, you can just return it
        return getattr(obj, 'member_count', obj.users.count())  # fallback to users count

    class Meta:
        model = Room
        fields = ['id', 'name', 'version', 'member_count', 'last_message']


class MessageRoomSerializer(serializers.ModelSerializer):
    class Meta:
        model = Room
        fields = ['id', 'version']


class MessageSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    room = MessageRoomSerializer(read_only=True)

    class Meta:
        model = Message
        fields = ['id', 'content', 'user', 'room', 'created_at']


class RoomMemberSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    room = RoomSerializer(read_only=True)

    class Meta:
        model = RoomMember
        fields = ['room', 'user']


# ----------------- VIEWS -----------------

class MessageListCreateAPIView(ListCreateAPIView):
    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        room_id = self.kwargs['room_id']
        # Ensure user is a member of the room
        get_object_or_404(RoomMember, user=self.request.user, room_id=room_id)
        return Message.objects.filter(
            room_id=room_id
        ).select_related('user', 'room').order_by('-created_at')

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        room_id = self.kwargs['room_id']
        get_object_or_404(RoomMember, user=request.user, room_id=room_id)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user, room_id=room_id)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class JoinRoomView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, room_id):
        room = get_object_or_404(Room.objects.select_for_update(), id=room_id)
        room.increment_version()

        if RoomMember.objects.filter(user=request.user, room=room).exists():
            return Response({"message": "already a member"}, status=status.HTTP_409_CONFLICT)

        obj, _ = RoomMember.objects.get_or_create(user=request.user, room=room)

        # You must define this method in your view
        channels = self.get_room_member_channels(room_id)
        obj.room.member_count = len(channels)

        body = RoomMemberSerializer(obj).data
        return Response(body, status=status.HTTP_200_OK)


class LeaveRoomView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, room_id):
        room = get_object_or_404(Room.objects.select_for_update(), id=room_id)
        room.increment_version()

        obj = get_object_or_404(RoomMember, user=request.user, room=room)

        channels = self.get_room_member_channels(room_id)
        obj.room.member_count = max(len(channels) - 1, 0)  # avoid negative

        pk = obj.pk
        obj.delete()

        body = RoomMemberSerializer(obj).data
        return Response(body, status=status.HTTP_200_OK)
