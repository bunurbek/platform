from django import forms
from .models import CustomUser


class RegisterForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Parol",
        widget=forms.PasswordInput(attrs={'placeholder': 'Parol kiriting', 'autocomplete': 'new-password'})
    )
    password2 = forms.CharField(
        label="Parolni tasdiqlang",
        widget=forms.PasswordInput(attrs={'placeholder': 'Parolni qayta kiriting', 'autocomplete': 'new-password'})
    )

    class Meta:
        model = CustomUser
        fields = ['full_name', 'email', 'phone']
        widgets = {
            'full_name': forms.TextInput(attrs={'placeholder': 'Ism Familiya'}),
            'email': forms.EmailInput(attrs={'placeholder': 'email@example.com', 'autocomplete': 'email'}),
            'phone': forms.TextInput(attrs={'placeholder': '+998 90 123 45 67'}),
        }
        labels = {
            'full_name': 'Ism Familiya',
            'email': 'Email',
            'phone': 'Telefon (ixtiyoriy)',
        }

    def clean_password2(self):
        p1 = self.cleaned_data.get('password1')
        p2 = self.cleaned_data.get('password2')
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Parollar mos kelmadi")
        return p2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
        return user


class LoginForm(forms.Form):
    email = forms.EmailField(
        label='Email',
        widget=forms.EmailInput(attrs={'placeholder': 'email@example.com', 'autocomplete': 'email'})
    )
    password = forms.CharField(
        label='Parol',
        widget=forms.PasswordInput(attrs={'placeholder': 'Parolingiz', 'autocomplete': 'current-password'})
    )
