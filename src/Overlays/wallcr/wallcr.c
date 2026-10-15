#include "Overlays/wallcr/wallcr.h"
#include "Game/MONSTER/MONAPI.h"
#include "Game/MONSTER/MONSTER.h"
#include "Game/MONSTER/MONLIB.h"
#include "Game/MONSTER/MONMSG.h"
#include "Game/PHYSICS.h"
#include "Game/MEMPACK.h"
#include "Game/MATH3D.h"
#include "Game/GAMELOOP.h"
#include "Game/STREAM.h"
#include "Game/PLAN/PLANAPI.h"
#include "Game/G2/ANMCTRLR.h"
#include "Game/PLAN/ENMYPLAN.h"
#include "Game/STATE.h"

void MON_MoveToPosition(Instance *instance, Position *position, short turnSpeed);

/* double-check that WALLCR_ShouldJumpOff and WALLCR_ShouldJumpOn aren't swapped */

// this conditional is for the objdiff report
#ifndef SKIP_ASM

void WALLCR_EnableControllers(Instance *instance)
{
    G2SVector3 extraRot;

    if (!G2Anim_IsControllerActive(&instance->anim, 1, G2ANIM_CTRLRTYPE_ADD_LOCALTRANS))
    {
        G2Anim_EnableController(&instance->anim, 1, G2ANIM_CTRLRTYPE_ADD_LOCALTRANS);
        extraRot.y = 0;
        extraRot.x = 0;
        extraRot.z = -436;
        G2Anim_SetController_Vector(&instance->anim, 1, G2ANIM_CTRLRTYPE_ADD_LOCALTRANS, &extraRot);
        instance->position.z += 436;
        G2Anim_EnableController(&instance->anim, 0, G2ANIM_CTRLRTYPE_ADD_LOCALROT);
        G2Anim_EnableController(&instance->anim, 18, G2ANIM_CTRLRTYPE_ADD_LOCALROT);
    }
}

void WALLCR_DisableControllers(Instance *instance)
{
    if (G2Anim_IsControllerActive(&instance->anim, 1, G2ANIM_CTRLRTYPE_ADD_LOCALTRANS))
    {
        G2Anim_DisableController(&instance->anim, 1, G2ANIM_CTRLRTYPE_ADD_LOCALTRANS);
        instance->position.z -= 436;
        G2Anim_DisableController(&instance->anim, 0, G2ANIM_CTRLRTYPE_ADD_LOCALROT);
        G2Anim_DisableController(&instance->anim, 18, G2ANIM_CTRLRTYPE_ADD_LOCALROT);
    }
}

void WALLCR_SetFlex(Instance *instance, int flex)
{
    G2SVector3 extraRot;

    extraRot.x = flex;
    extraRot.z = 0;
    extraRot.y = 0;
    G2Anim_SetController_Vector(&instance->anim, 18, G2ANIM_CTRLRTYPE_ADD_LOCALROT, &extraRot);
}

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_CollideFace);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_NearFloor);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_FindJumpOnPoint);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_AngleFromPosToPos);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_FollowWall);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_MoveForward);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_ApplySpin);

INCLUDE_RODATA("asm/nonmatchings/Overlays/wallcr/wallcr", D_88000000);

INCLUDE_RODATA("asm/nonmatchings/Overlays/wallcr/wallcr", D_88000020);

INCLUDE_RODATA("asm/nonmatchings/Overlays/wallcr/wallcr", D_88000038);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_AttachEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_Attach);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_DetachEntry);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_Detach);

int WALLCR_SeekPath(Instance *instance, Position *outputPos, Position *targetPos, int unused, int validNodeTypes)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;
    int result;

    (void)unused;

    result = ENMYPLAN_MoveToTargetFinal(instance, outputPos, mv->pathSlotID, targetPos, validNodeTypes);
    if (result == 3)
    {
        MON_GetPlanSlot(mv);
    }

    return result;
}

int WALLCR_ShouldJumpOff(Instance *instance)
{
    int nodeType;

    nodeType = ENMYPLAN_GetNodeTypeOfNextWaypoint(((MonsterVars *)instance->extraData)->pathSlotID);
    if (nodeType != 64 && ((nodeType >> 3) & 3) == 1)
    {
        return 1;
    }

    return 0;
}

int WALLCR_ShouldJumpOn(Instance *instance)
{
    int nodeType;

    nodeType = ENMYPLAN_GetNodeTypeOfNextWaypoint(((MonsterVars *)instance->extraData)->pathSlotID);
    if (nodeType != 64 && ((nodeType >> 3) & 3) == 0)
    {
        return 1;
    }

    return 0;
}

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_ShouldSurpriseAttack);

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_MoveToDestination);

void WALLCR_Message(Instance *instance, unsigned long message, unsigned long data)
{
    MonsterMessage(instance, message, data);
}

uintptr_t WALLCR_Query(Instance *instance, unsigned long query)
{
    uintptr_t ret;

    if (query == 13)
    {
        if (instance->matrix != NULL)
        {
            ret = (uintptr_t)instance->matrix;
        }
        else
        {
            ret = 0;
        }
    }
    else
    {
        ret = MonsterQuery(instance, query);
    }

    return ret;
}

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_Init);

void WALLCR_CleanUp(Instance *instance)
{
    MonsterVars *mv;

    G2Anim_DetachControllerFromSeg(&instance->anim, 1, G2ANIM_CTRLRTYPE_ADD_LOCALTRANS);
    G2Anim_DetachControllerFromSeg(&instance->anim, 0, G2ANIM_CTRLRTYPE_ADD_LOCALROT);
    G2Anim_DetachControllerFromSeg(&instance->anim, 18, G2ANIM_CTRLRTYPE_ADD_LOCALROT);

    mv = (MonsterVars *)instance->extraData;
    if (mv != NULL && mv->extraVars != NULL)
    {
        MEMPACK_Free(mv->extraVars);
    }

    MON_CleanUp(instance);
}

void WALLCR_PursueEntry(Instance *instance)
{
    MonsterVars *mv;
    MonsterIR *enemy;

    mv = (MonsterVars *)instance->extraData;
    enemy = mv->enemy;

    if ((mv->auxFlags & 4) && !(mv->mvFlags & 4))
    {
        MON_GetPlanSlot(mv);
        if (enemy != NULL)
        {
            COPY_SVEC(Position, &mv->destination, Position, &enemy->instance->position);
        }
        else
        {
            COPY_SVEC(Position, &mv->destination, Position, &instance->position);
        }
        mv->mode = 4;
        mv->mvFlags &= ~0x20000;
    }
    else
    {
        MON_PursueEntry(instance);
    }
}

void WALLCR_Pursue(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;
    MonsterIR *enemy = mv->enemy;

    if (mv->mvFlags & 4)
    {
        if (mv->auxFlags & 4)
        {
            MON_SwitchState(instance, MONSTER_STATE_IDLE);
            MON_DefaultQueueHandler(instance);
        }
        else
        {
            MON_Pursue(instance);
        }
    }
    else
    {
        mv->auxFlags &= ~0x20;
        if (WALLCR_ShouldSurpriseAttack(instance))
        {
            mv->auxFlags = (mv->auxFlags | 0x20) & ~0x10;
            MON_SwitchState(instance, MONSTER_STATE_SURPRISEATTACK);
            MON_DefaultQueueHandler(instance);
        }
        else if (WALLCR_MoveToDestination(instance, NULL))
        {
            if (enemy != NULL)
            {
                COPY_SVEC(Position, &mv->destination, Position, &enemy->instance->position);
            }
        }
        else if (instance->currentMainState == MONSTER_STATE_PURSUE)
        {
            MON_Pursue(instance);
        }
    }
}

void WALLCR_SurpriseAttackEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    if (mv->auxFlags & 0x20)
    {
        MON_GetPlanSlot(mv);
    }
    else
    {
        MON_SurpriseAttackEntry(instance);
    }
}

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_SurpriseAttack);

void WALLCR_IdleEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    if (mv->auxFlags & 4)
    {
        MON_PlayAnimFromList(instance, ((MonsterAttributes *)instance->data)->auxAnimList, 6, 2);
    }
    else
    {
        MON_PlayRandomIdle(instance, 2);
        MON_IdleEntry(instance);
    }
}

int WALLCR_ShouldIAmbushPlayer(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;
    Instance *player = gameTrackerX.playerInstance;
    Position pos;

    if ((signed char)mv->ambushMarker != 0 && mv->ambushArc == 0x800 && mv->ambushElevation == 0x400)
    {
        if (!PLANAPI_FindNodePositionInUnit(STREAM_GetStreamUnitWithID(instance->birthStreamUnitID),
                                            &pos, (signed char)mv->ambushMarker, 4))
        {
            return 0;
        }

        return MATH3D_LengthXYZ(player->position.x - pos.x,
                                player->position.y - pos.y,
                                player->position.z - pos.z) < mv->ambushRange;
    }

    if (instance->currentMainState == MONSTER_STATE_PUPATE)
    {
        return 0;
    }

    return MATH3D_LengthXYZ(player->position.x - instance->position.x,
                            player->position.y - instance->position.y,
                            player->position.z - instance->position.z) < mv->ambushRange;
}

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_Idle);

void WALLCR_WanderEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    if (mv->mvFlags & 4)
    {
        MON_GetPlanSlot(mv);
    }
    else if (!(mv->auxFlags & 4))
    {
        MON_WanderEntry(instance);
    }
    else
    {
        WALLCR_DetachEntry(instance, NULL);
    }
}

void WALLCR_Wander(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    if (mv->mvFlags & 4)
    {
        Position target;

        if (!WALLCR_MoveToDestination(instance, &target))
        {
            if (!MON_AnimPlaying(instance, MONSTER_ANIM_RUN))
            {
                MON_PlayAnim(instance, MONSTER_ANIM_RUN, 2);
            }
            MON_MoveToPosition(instance, &target, mv->subAttr->speedRunTurn);
            mv->mvFlags |= 0x20000;
        }

        if (instance->currentMainState != MONSTER_STATE_FALL &&
            MATH3D_LengthXYZ(instance->position.x - mv->destination.x,
                             instance->position.y - mv->destination.y,
                             instance->position.z - mv->destination.z) < 300)
        {
            MON_SwitchState(instance, MONSTER_STATE_IDLE);
        }
    }
    else if (mv->auxFlags & 1)
    {
        MON_DefaultQueueHandler(instance);
        WALLCR_Detach(instance);
    }
    else
    {
        MON_Wander(instance);
    }
}

void WALLCR_FallEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    if (mv->auxFlags & 4)
    {
        MON_SwitchStateDoEntry(instance, MONSTER_STATE_IDLE);
    }
    else
    {
        MON_FallEntry(instance);
    }
}

void WALLCR_Fall(Instance *instance)
{
    MON_Fall(instance);
}

INCLUDE_ASM("asm/nonmatchings/Overlays/wallcr/wallcr", WALLCR_ImpactEntry);

void WALLCR_Impact(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    if (mv->extraVars != NULL)
    {
        if (MON_AnimPlayingFromList(instance, ((MonsterAttributes *)instance->data)->auxAnimList, 2))
        {
            if (instance->flags2 & 0x10)
            {
                mv->auxFlags = (mv->auxFlags & ~1) | 4;
                MON_SwitchState(instance, MONSTER_STATE_IDLE);
            }
            MON_DefaultQueueHandler(instance);
        }
        else
        {
            MON_Impact(instance);
        }
    }
}

void WALLCR_GeneralDeathEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    WALLCR_DisableControllers(instance);
    mv->mvFlags &= ~0x800;
    mv->auxFlags &= ~4;
    instance->zAccl = -16;
    instance->rotation.y = 0;
    instance->rotation.x = 0;
    MON_GeneralDeathEntry(instance);
}

void WALLCR_GeneralDeath(Instance *instance)
{
    MON_GeneralDeath(instance);
}

void WALLCR_HitEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    WALLCR_DisableControllers(instance);
    mv->mvFlags &= ~0x800;
    mv->auxFlags &= ~4;
    instance->zAccl = -16;
    instance->rotation.y = 0;
    instance->rotation.x = 0;
    MON_HitEntry(instance);
}

void WALLCR_Hit(Instance *instance)
{
    MON_Hit(instance);
}

void WALLCR_MissileHitEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    WALLCR_DisableControllers(instance);
    mv->mvFlags &= ~0x800;
    mv->auxFlags &= ~4;
    instance->zAccl = -16;
    instance->rotation.y = 0;
    instance->rotation.x = 0;
    MON_MissileHitEntry(instance);
}

void WALLCR_MissileHit(Instance *instance)
{
    MON_MissileHit(instance);
}

void WALLCR_NoticeEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    WALLCR_DisableControllers(instance);
    mv->mvFlags &= ~0x800;
    mv->auxFlags &= ~4;
    instance->zAccl = -16;
    instance->rotation.y = 0;
    instance->rotation.x = 0;
    MON_NoticeEntry(instance);
}

void WALLCR_Notice(Instance *instance)
{
    MON_Notice(instance);
}

void WALLCR_SurprisedEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    WALLCR_DisableControllers(instance);
    mv->mvFlags &= ~0x800;
    mv->auxFlags &= ~4;
    instance->zAccl = -16;
    instance->rotation.y = 0;
    instance->rotation.x = 0;
    MON_SurprisedEntry(instance);
}

void WALLCR_Surprised(Instance *instance)
{
    MON_Surprised(instance);
}

void WALLCR_StunnedEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    WALLCR_DisableControllers(instance);
    mv->mvFlags &= ~0x800;
    mv->auxFlags &= ~4;
    instance->zAccl = -16;
    instance->rotation.y = 0;
    instance->rotation.x = 0;
    MON_StunnedEntry(instance);
}

void WALLCR_Stunned(Instance *instance)
{
    MON_Stunned(instance);
}

void WALLCR_ThrownEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    WALLCR_DisableControllers(instance);
    mv->mvFlags &= ~0x800;
    mv->auxFlags &= ~4;
    instance->zAccl = -16;
    instance->rotation.y = 0;
    instance->rotation.x = 0;
    MON_ThrownEntry(instance);
}

void WALLCR_Thrown(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;

    if (!(mv->mvFlags & 2))
    {
        MessageQueue tempQueue;
        Message *message;

        InitMessageQueue(&tempQueue);
        while ((message = DeMessageQueue(&mv->messageQueue)) != NULL)
        {
            if (message->ID != 0x100000E && message->ID != 0x1000012)
            {
                EnMessageQueueData(&tempQueue, message->ID, message->Data);
            }
        }

        while ((message = DeMessageQueue(&tempQueue)) != NULL)
        {
            EnMessageQueueData(&mv->messageQueue, message->ID, message->Data);
        }
    }

    MON_Thrown(instance);
}

void WALLCR_PupateEntry(Instance *instance)
{
    MON_PupateEntry(instance);
}

void WALLCR_Pupate(Instance *instance)
{
    MON_Pupate(instance);

    if (instance->currentMainState != MONSTER_STATE_PUPATE)
    {
        PhysicsSetVelFromRot(instance, &instance->rotation, 15);
    }
}

#else

void WALLCR_EnableControllers(void) { };

void WALLCR_DisableControllers(void) { };

void WALLCR_SetFlex(void) { };

void WALLCR_CollideFace(void) { };

void WALLCR_NearFloor(void) { };

void WALLCR_FindJumpOnPoint(void) { };

void WALLCR_AngleFromPosToPos(void) { };

void WALLCR_FollowWall(void) { };

void WALLCR_MoveForward(void) { };

void WALLCR_ApplySpin(void) { };

void WALLCR_AttachEntry(void) { };

void WALLCR_Attach(void) { };

void WALLCR_DetachEntry(void) { };

void WALLCR_Detach(void) { };

void WALLCR_SeekPath(void) { };

void WALLCR_ShouldJumpOff(void) { };

void WALLCR_ShouldJumpOn(void) { };

void WALLCR_ShouldSurpriseAttack(void) { };

void WALLCR_MoveToDestination(void) { };

void WALLCR_Message(void) { };

void WALLCR_Query(void) { };

void WALLCR_Init(void) { };

void WALLCR_CleanUp(void) { };

void WALLCR_PursueEntry(void) { };

void WALLCR_Pursue(void) { };

void WALLCR_SurpriseAttackEntry(void) { };

void WALLCR_SurpriseAttack(void) { };

void WALLCR_IdleEntry(void) { };

void WALLCR_ShouldIAmbushPlayer(void) { };

void WALLCR_Idle(void) { };

void WALLCR_WanderEntry(void) { };

void WALLCR_Wander(void) { };

void WALLCR_FallEntry(void) { };

void WALLCR_Fall(void) { };

void WALLCR_ImpactEntry(void) { };

void WALLCR_Impact(void) { };

void WALLCR_GeneralDeathEntry(void) { };

void WALLCR_GeneralDeath(void) { };

void WALLCR_HitEntry(void) { };

void WALLCR_Hit(void) { };

void WALLCR_MissileHitEntry(void) { };

void WALLCR_MissileHit(void) { };

void WALLCR_NoticeEntry(void) { };

void WALLCR_Notice(void) { };

void WALLCR_SurprisedEntry(void) { };

void WALLCR_Surprised(void) { };

void WALLCR_StunnedEntry(void) { };

void WALLCR_Stunned(void) { };

void WALLCR_ThrownEntry(void) { };

void WALLCR_Thrown(void) { };

void WALLCR_PupateEntry(void) { };

void WALLCR_Pupate(void) { };

#endif
