from django.contrib.auth.views import LoginView, LogoutView


class UserLoginView(LoginView):
    template_name = "accounts/login.html"
    redirect_authenticated_user = True


class UserLogoutView(LogoutView):
    next_page = "accounts:login"
