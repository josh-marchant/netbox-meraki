"""URL patterns for NetBox Meraki plugin"""
from django.urls import path
from . import views


urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('sync/', views.SyncView.as_view(), name='sync'),
    path('sync/<int:pk>/', views.SyncLogView.as_view(), name='synclog'),
    path('config/', views.ConfigView.as_view(), name='config'),
    path('job-history/', views.JobHistoryView.as_view(), name='job_history'),
    
    path('site-rules/', views.SiteNameRuleListView.as_view(), name='sitenamerule_list'),
    path('site-rules/add/', views.SiteNameRuleCreateView.as_view(), name='sitenamerule_add'),
    path('site-rules/<int:pk>/edit/', views.SiteNameRuleUpdateView.as_view(), name='sitenamerule_edit'),
    path('site-rules/<int:pk>/delete/', views.SiteNameRuleDeleteView.as_view(), name='sitenamerule_delete'),
    
    path('prefix-filters/', views.PrefixFilterRuleListView.as_view(), name='prefixfilterrule_list'),
    path('prefix-filters/add/', views.PrefixFilterRuleCreateView.as_view(), name='prefixfilterrule_add'),
    path('prefix-filters/<int:pk>/edit/', views.PrefixFilterRuleUpdateView.as_view(), name='prefixfilterrule_edit'),
    path('prefix-filters/<int:pk>/delete/', views.PrefixFilterRuleDeleteView.as_view(), name='prefixfilterrule_delete'),

    path('vlan-rules/', views.MerakiVLANResolutionRuleListView.as_view(), name='vlanresolutionrule_list'),
    path('vlan-rules/add/', views.MerakiVLANResolutionRuleCreateView.as_view(), name='vlanresolutionrule_add'),
    path('vlan-rules/<int:pk>/edit/', views.MerakiVLANResolutionRuleUpdateView.as_view(), name='vlanresolutionrule_edit'),
    path('vlan-rules/<int:pk>/delete/', views.MerakiVLANResolutionRuleDeleteView.as_view(), name='vlanresolutionrule_delete'),
    
    path('reviews/', views.ReviewListView.as_view(), name='review_list'),
    path('reviews/clear/', views.ReviewBulkClearView.as_view(), name='review_bulk_clear'),
    path('review/<int:pk>/', views.ReviewDetailView.as_view(), name='review_detail'),
    path('review/<int:pk>/bulk-action/', views.ReviewBulkActionView.as_view(), name='review_bulk_action'),
    path('review/<int:pk>/clear/', views.ReviewClearView.as_view(), name='review_clear'),
    path('review/<int:pk>/item/<int:item_pk>/action/', views.ReviewItemActionView.as_view(), name='review_item_action'),
    path('review/<int:pk>/item/<int:item_pk>/edit/', views.ReviewItemEditView.as_view(), name='review_item_edit'),
    path('review/<int:pk>/apply/', views.ReviewApplyView.as_view(), name='review_apply'),
    
    path('api/sync/<int:pk>/progress/', views.SyncProgressAPIView.as_view(), name='sync_progress_api'),
    path('api/sync/<int:pk>/cancel/', views.SyncCancelAPIView.as_view(), name='sync_cancel_api'),
    path('api/sync/<int:pk>/status/', views.SyncProgressAPIView.as_view(), name='sync_status_api'),
    path('api/networks/<str:org_id>/', views.NetworksAPIView.as_view(), name='get_networks'),
    path('api/organizations/', views.OrganizationsAPIView.as_view(), name='get_organizations'),
    
    path('scheduled-sync/', views.ScheduledSyncView.as_view(), name='scheduled_sync'),
    path('scheduled-sync/<int:pk>/edit/', views.ScheduledSyncEditView.as_view(), name='scheduled_sync_edit'),
    path('scheduled-sync/<int:pk>/delete/', views.ScheduledSyncDeleteView.as_view(), name='scheduled_sync_delete'),
    path('scheduled-sync/<int:pk>/toggle/', views.ScheduledSyncToggleView.as_view(), name='scheduled_sync_toggle'),
]
