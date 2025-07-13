import hashlib
import hmac
import json
import os
import time
from django.shortcuts import render
from rest_framework import viewsets
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
import requests
from django.conf import settings
from listings.models import Property, Booking, Payment, Review, Message
from listings.serializers import (
    PropertySerializer, BookingSerializer, PaymentSerializer, ReviewSerializer, MessageSerializer
)

# Import the send_payment_confirmation_email task/function
from listings.tasks import send_booking_confirmation_email, send_payment_confirmation_email

class PropertyViewSet(viewsets.ModelViewSet):
    queryset = Property.objects.all()
    serializer_class = PropertySerializer
class BookingViewSet(viewsets.ModelViewSet):
    queryset = Booking.objects.all()
    serializer_class = BookingSerializer
    def perform_create(self, serializer):
        booking = serializer.save()
        user_email = booking.user.email
        send_booking_confirmation_email.delay(user_email, booking.id)
class PaymentViewSet(viewsets.ModelViewSet):
    queryset = Payment.objects.all()
    serializer_class = PaymentSerializer

    @action(detail=False, methods=['post'])
    def initiate_payment(self, request):
        """
        Initiate payment with Chapa API
        """
        # Validate booking data
        booking_serializer = BookingSerializer(data=request.data)
        booking_serializer.is_valid(raise_exception=True)
        booking_data = booking_serializer.validated_data

        # Prepare Chapa API request
        tx_ref = f"booking-{int(time.time())}"  # Unique transaction reference
        chapa_payload = {
            "amount": str(booking_data['total_price']),
            "currency": "ETB",
            "email": booking_data['user'].email,
            "first_name": booking_data['user'].first_name,
            "last_name": booking_data['user'].last_name,
            "tx_ref": tx_ref,
            "callback_url": f"{settings.CHAPA_API}/transaction/initialize",
            "return_url": booking_data.get('return_url', f"{settings.FRONTEND_URL}/payment-complete"),
            "customization": {
                "title": "Property Booking Payment",
                "description": f"Payment for booking {booking_data['property'].title}"
            },
            "meta": {
                "booking_id": str(booking_data['id']),
                "user_id": str(booking_data['user'].id)
            }
        }

        # Create pending payment record
        payment = Payment.objects.create(
            booking=booking_data['property'],
            user=booking_data['user'],
            amount=booking_data['total_price'],
            transaction_id=tx_ref,
            status='Pending',
            payment_method='Chapa'
        )

        # Call Chapa API
        headers = {
            "Authorization": f"Bearer {settings.CHAPA_SECRET_KEY}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(
                settings.CHAPA_API + "/transaction/initialize",
                json=chapa_payload,
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            chapa_response = response.json()

            # Update payment with Chapa response
            payment.transaction_id = chapa_response.get('data', {}).get('id')
            payment.checkout_url = chapa_response.get('data', {}).get('checkout_url')
            payment.metadata = {
                'chapa_response': chapa_response,
                'init_data': booking_data
            }
            payment.save()

            return Response({
                'payment_url': payment.checkout_url,
                'transaction_id': payment.transaction_id,
                'status': payment.status
            }, status=status.HTTP_201_CREATED)

        except requests.exceptions.RequestException as e:
            payment.status = 'Failed'
            payment.metadata['error'] = str(e)
            payment.save()
            return Response(
                {'error': 'Payment initiation failed', 'details': str(e)},
                status=status.HTTP_502_BAD_GATEWAY
            )

    @action(detail=False, methods=['post'], url_path='verify')
    def verify_payment(self, request):
        """
        Verify payment status with Chapa API
        """
        transaction_id = request.data.get('transaction_id')
        if not transaction_id:
            return Response(
                {'error': 'transaction_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            payment = Payment.objects.get(transaction_id=transaction_id)
            
            # If payment is already completed, return current status
            if payment.status == 'Completed':
                return Response({
                    'status': payment.status,
                    'transaction_id': payment.transaction_id
                })

            # Verify with Chapa API
            headers = {
                "Authorization": f"Bearer {settings.CHAPA_SECRET_KEY}"
            }
            response = requests.get(
                f"{settings.CHAPA_API}/transaction/verify/{payment.chapa_transaction_id}/",
                headers=headers,
                timeout=30
            )
            response.raise_for_status()
            verification_data = response.json()

            # Update payment status based on verification
            if verification_data.get('status') == 'success':
                payment.status = 'Completed'
                # Trigger confirmation email
                send_payment_confirmation_email.delay(
                    payment.user.email,
                    payment.booking.id,
                    payment.amount
                )
            else:
                payment.status = 'Failed'
            
            payment.metadata['verification_data'] = verification_data
            payment.save()

            return Response({
                'status': payment.status,
                'transaction_id': payment.transaction_id,
                'verification_data': verification_data
            })

        except Payment.DoesNotExist:
            return Response(
                {'error': 'Payment not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except requests.exceptions.RequestException as e:
            return Response(
                {'error': 'Verification failed', 'details': str(e)},
                status=status.HTTP_502_BAD_GATEWAY
            )

    @action(detail=False, methods=['post'], url_path='webhook')
    def chapa_webhook(self, request):
        """
        Handle Chapa webhook notifications
        """
        # Verify signature
        signature = request.headers.get('x-chapa-signature') or request.headers.get('Chapa-Signature')
        if not self._verify_chapa_signature(request.data, signature):
            return Response({'error': 'Invalid signature'}, status=status.HTTP_403_FORBIDDEN)

        event = request.data.get('event')
        tx_ref = request.data.get('tx_ref')
        chapa_transaction_id = request.data.get('id') or request.data.get('reference')

        try:
            payment = Payment.objects.get(transaction_id=tx_ref)
            
            if event == 'charge.success':
                payment.status = 'Completed'
                payment.chapa_transaction_id = chapa_transaction_id
                payment.metadata['webhook_data'] = request.data
                payment.save()
                
                # Trigger confirmation email
                send_payment_confirmation_email.delay(
                    payment.user.email,
                    payment.booking.id,
                    payment.amount
                )
                
            elif event in ['charge.failure', 'charge.expired']:
                payment.status = 'Failed'
                payment.chapa_transaction_id = chapa_transaction_id
                payment.metadata['webhook_data'] = request.data
                payment.save()
                
            elif event == 'charge.refunded':
                payment.status = 'Refunded'
                payment.chapa_transaction_id = chapa_transaction_id
                payment.metadata['webhook_data'] = request.data
                payment.save()

            return Response({'status': 'Webhook processed'}, status=status.HTTP_200_OK)

        except Payment.DoesNotExist:
            return Response({'error': 'Payment not found'}, status=status.HTTP_404_NOT_FOUND)

    def _verify_chapa_signature(self, payload, signature):
        """
        Verify Chapa webhook signature
        """
        if not signature or not settings.CHAPA_WEBHOOK_SECRET:
            return False

        payload_str = json.dumps(payload, separators=(',', ':')) if isinstance(payload, dict) else payload
        computed_signature = hmac.new(
            key=settings.CHAPA_WEBHOOK_SECRET.encode('utf-8'),
            msg=payload_str.encode('utf-8'),
            digestmod=hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(computed_signature, signature)
        
class ReviewViewSet(viewsets.ModelViewSet):
    queryset = Review.objects.all()
    serializer_class = ReviewSerializer
class MessageViewSet(viewsets.ModelViewSet):
    queryset = Message.objects.all()
    serializer_class = MessageSerializer