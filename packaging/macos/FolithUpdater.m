// Thin C boundary for the shared Qt application. Sparkle owns its native UI.
#import <Cocoa/Cocoa.h>
#import <Sparkle/Sparkle.h>

typedef void (*RestartCallback)(void);
@interface FolithUpdateDelegate : NSObject <SPUUpdaterDelegate>
@property(nonatomic, copy) void (^installHandler)(void);
@property(nonatomic) RestartCallback restartCallback;
@end

@implementation FolithUpdateDelegate
- (BOOL)updater:(SPUUpdater *)updater shouldPostponeRelaunchForUpdate:(SUAppcastItem *)item
    untilInvokingBlock:(void (^)(void))installHandler {
    self.installHandler = installHandler;
    // Returning YES prevents Sparkle from terminating before Qt saves/stops.
    if (self.restartCallback) self.restartCallback();
    return YES;
}
@end

static SPUStandardUpdaterController *controller;
static FolithUpdateDelegate *delegate;

int folith_updater_start(RestartCallback callback) {
    if (![NSThread isMainThread]) return 0;
    if (controller) return 1;
    delegate = [FolithUpdateDelegate new];
    delegate.restartCallback = callback;
    controller = [[SPUStandardUpdaterController alloc] initWithStartingUpdater:NO
        updaterDelegate:delegate userDriverDelegate:nil];
    NSError *error = nil;
    if ([controller.updater startUpdater:&error]) return 1;
    controller = nil;
    delegate = nil;
    return 0;
}

void folith_updater_check(void) {
    if (delegate.installHandler) {
        if (delegate.restartCallback) delegate.restartCallback();
    } else {
        [controller checkForUpdates:nil];
    }
}

void folith_updater_resume(void) {
    void (^handler)(void) = delegate.installHandler;
    delegate.installHandler = nil;
    if (handler) handler();
}
