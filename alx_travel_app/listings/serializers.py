from rest_framework import serializers
from .models import Role, User, Property, Booking, Payment, Review, Message

class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ['role_id', 'role_name']

class UserSerializer(serializers.ModelSerializer):
    role = RoleSerializer(read_only=True)
    role_id = serializers.PrimaryKeyRelatedField(
        queryset=Role.objects.all(), 
        source='role',
        write_only=True
    )
    
    class Meta:
        model = User
        fields = ['user_id', 'role', 'role_id', 'first_name', 'last_name', 'email', 'phone_number', 'created_at']
        extra_kwargs = {
            'password': {'write_only': True},
            'email': {'required': True},
            'first_name': {'required': True},
            'last_name': {'required': True},
        }

class PropertySerializer(serializers.ModelSerializer):
    class Meta:
        model = Property
        fields = '__all__'
        read_only_fields = ['host', 'created_at', 'updated_at']

class BookingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = ['booking_id', 'user', 'property', 'check_in_date', 'check_out_date', 'total_price', 'created_at']
        read_only_fields = ['user', 'total_price', 'created_at']

class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ['payment_id', 'booking', 'amount', 'payment_date', 'status']
        read_only_fields = ['payment_date']
        extra_kwargs = {
            'status': {'default': 'Pending'}
        }


class ReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ['review_id', 'user', 'property', 'rating', 'comment', 'created_at']
        read_only_fields = ['user', 'created_at']

class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ['message_id', 'sender', 'receiver', 'content', 'sent_at']
        read_only_fields = ['sender', 'sent_at']