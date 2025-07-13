from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings

@shared_task
def send_payment_confirmation_email(email, booking_id, amount):
    subject = 'Payment Confirmation'
    message = (
        f'Your payment of {amount} ETB for booking #{booking_id} has been confirmed.\n\n'
        'Thank you for your booking!'
    )
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [email],
        fail_silently=False,
    )

@shared_task
def send_booking_confirmation_email(user_email, booking_id):
    subject = 'Booking Confirmation'
    message = f'Thank you for your booking. Your booking ID is {booking_id}.'
    from_email = settings.DEFAULT_FROM_EMAIL
    recipient_list = [user_email]

    send_mail(subject, message, from_email, recipient_list)
