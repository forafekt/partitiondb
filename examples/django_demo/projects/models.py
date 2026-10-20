from django.db import models


class Note(models.Model):
    body = models.CharField(max_length=200)
