from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_POST
import json, resend
from django.conf import settings
from .models import ContactMessage

def contact(request):
    return render(request, 'contact/contact.html')

@require_POST
def send_message(request):
    try:
        data = json.loads(request.body)
        msg = ContactMessage.objects.create(
            name=data.get('name', ''),
            email=data.get('email', ''),
            phone=data.get('phone', ''),
            subject=data.get('subject', ''),
            message=data.get('message', ''),
        )
        if settings.RESEND_API_KEY:
            try:
                resend.api_key = settings.RESEND_API_KEY
                resend.Emails.send({
                    "from": settings.DEFAULT_FROM_EMAIL,
                    "to": [settings.COMPANY_EMAIL],
                    "subject": f"Contact Form: {msg.subject}",
                    "html": f"<p>Name: {msg.name}</p><p>Email: {msg.email}</p><p>Phone: {msg.phone}</p><p>Message: {msg.message}</p>",
                })
            except Exception:
                pass
        return JsonResponse({'success': True, 'message': 'Message sent successfully!'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)
