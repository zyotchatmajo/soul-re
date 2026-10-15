#ifndef _WALLCR_H_
#define _WALLCR_H_

#include "common.h"

// size: 0x4
typedef struct _WallcrAttributes
{
    // offset: 0x0000
    int surprise_attack_time;
} WallcrAttributes;

// size: 0x14
typedef struct _WallcrVars
{
    // offset: 0x0000
    short yaw_speed;
    // offset: 0x0002
    short pitch_speed;
    // offset: 0x0004
    short roll_speed;
    // offset: 0x0006
    short roll;
    // offset: 0x0008
    int correct_roll_speed;
    // offset: 0x000C
    int time;
    // offset: 0x0010
    short left_over_time;
    // offset: 0x0012
    short wallcrFlags;
} WallcrVars;

void WALLCR_EnableControllers(Instance *instance);
void WALLCR_DisableControllers(Instance *instance);
void WALLCR_SetFlex(Instance *instance, int flex);
void WALLCR_DetachEntry(Instance *instance, Position *target);
void WALLCR_Detach(Instance *instance);
int WALLCR_ShouldSurpriseAttack(Instance *instance);
int WALLCR_MoveToDestination(Instance *instance, Position *target);

#endif
