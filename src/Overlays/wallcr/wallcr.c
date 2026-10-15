#include "Overlays/wallcr/wallcr.h"
#include "Game/MONSTER/MONAPI.h"
#include "Game/MONSTER/MONSTER.h"
#include "Game/MONSTER/MONLIB.h"
#include "Game/MONSTER/MONMSG.h"
#include "Game/MONSTER/MONSENSE.h"
#include "Game/PHYSICS.h"
#include "Game/MEMPACK.h"
#include "Game/MATH3D.h"
#include "Game/GAMELOOP.h"
#include "Game/STREAM.h"
#include "Game/PLAN/PLANAPI.h"
#include "Game/COLLIDE.h"
#include "Game/G2/ANMCTRLR.h"
#include "Game/PLAN/ENMYPLAN.h"
#include "Game/STATE.h"

void MON_MoveToPosition(Instance *instance, Position *position, short turnSpeed);

/* double-check that WALLCR_ShouldJumpOff and WALLCR_ShouldJumpOn aren't swapped */

INCLUDE_RODATA("asm/nonmatchings/Overlays/wallcr/wallcr", D_88000000);

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

void *WALLCR_CollideFace(Level *level, Position *start, Position *end, Position *hit, Position *normal)
{
    PCollideInfo info;
    Position oldPoint;
    Position newPoint;
    void *result;

    oldPoint.x = start->x;
    oldPoint.y = start->y;
    oldPoint.z = start->z;
    newPoint.x = end->x;
    newPoint.y = end->y;
    newPoint.z = end->z;
    info.oldPoint = (SVECTOR *)&oldPoint;
    info.newPoint = (SVECTOR *)&newPoint;
    info.inst = NULL;
    info.collideType = 1;
    info.instance = NULL;
    result = NULL;
    COLLIDE_PointAndWorld(&info, level);

    if (info.type == 3)
    {
        result = info.prim;
        if (hit != NULL)
        {
            hit->x = newPoint.x;
            hit->y = newPoint.y;
            hit->z = newPoint.z;
        }
        if (normal != NULL)
        {
            normal->x = info.wNormal.vx;
            normal->y = info.wNormal.vy;
            normal->z = info.wNormal.vz;
        }
    }

    return result;
}

int WALLCR_NearFloor(Instance *instance)
{
    Level *level;
    Position start;
    Position end;

    level = STREAM_GetLevelWithID(instance->currentStreamUnitID);
    if (level != NULL)
    {
        COPY_SVEC(Position, &start, Position, &instance->position);
        COPY_SVEC(Position, &end, Position, &start);
        end.z -= 320;
        if (WALLCR_CollideFace(level, &start, &end, NULL, NULL) != NULL)
        {
            return 1;
        }
    }

    return 0;
}

void *WALLCR_FindJumpOnPoint(Instance *instance, Position *position, Position *normal)
{
    Level *level;
    void *result = NULL;
    int i = 0;
    Position start;
    Position end;
    Position offset;
    Position point;

    level = STREAM_GetLevelWithID(instance->currentStreamUnitID);
    if (level != NULL)
    {
        COPY_SVEC(Position, &point, Position, position);
        offset.x = 320;
        offset.y = 0;
        start.z = point.z;
        end.z = point.z;

        for (i = 0; i < 4; i++)
        {
            start.x = point.x - offset.x;
            start.y = point.y - offset.y;
            end.x = point.x + offset.x;
            end.y = point.y + offset.y;
            result = WALLCR_CollideFace(level, &start, &end, position, normal);
            if (result != NULL)
            {
                break;
            }

            offset.y ^= offset.x;
            offset.x ^= offset.y;
            offset.y ^= offset.x;
            offset.x = -offset.x;
        }
    }

    return result;
}

int WALLCR_AngleFromPosToPos(Position *oldPos, Position *newPos, Rotation *rotation)
{
    MATRIX matrix;
    MATRIX transposed;
    Position offset;

    matrix.m[0][0] = 4096;
    matrix.m[0][1] = 0;
    matrix.m[0][2] = 0;
    matrix.m[1][0] = 0;
    matrix.m[1][1] = 4096;
    matrix.m[1][2] = 0;
    matrix.m[2][0] = 0;
    matrix.m[2][1] = 0;
    matrix.m[2][2] = 4096;

    RotMatrixZ(rotation->z, &matrix);
    RotMatrixY(rotation->y, &matrix);
    RotMatrixX(rotation->x, &matrix);
    TransposeMatrix(&matrix, &transposed);

    COPY_SVEC(Position, &offset, Position, newPos);
    offset.x -= oldPos->x;
    offset.y -= oldPos->y;
    offset.z -= oldPos->z;
    ApplyMatrixSV(&transposed, (SVECTOR *)&offset, (SVECTOR *)&offset);

    return (ratan2(offset.z, -offset.x) + 3072) & 0xFFF;
}

int WALLCR_FollowWall(Instance *instance)
{
    int flex = 0;
    int i;
    WallcrVars *vars = (WallcrVars *)((MonsterVars *)instance->extraData)->extraVars;
    Level *level = STREAM_GetLevelWithID(instance->currentStreamUnitID);
    SVECTOR points[3] = {{0, 0, 0, 0}, {0, 250, 250, 0}, {0, 250, -250, 0}};
    MATRIX matrix;
    TFace *tface[2];
    Position hit[2];
    Normal normals[2];
    Rotation rotation;
    G2SVector3 extraRot;
    unsigned char edges[4][2] = {{0, 1}, {0, 2}, {1, 2}, {2, 1}};
    int dist[2];
    Position pos;
    Normal normal;
    SVECTOR in;
    SVECTOR out;
    Position zero = {0};
    int count;
    int total;
    Normal *ref;
    int upsideDown;

    matrix.m[0][0] = 4096;
    matrix.m[0][1] = 0;
    matrix.m[0][2] = 0;
    matrix.m[1][0] = 0;
    matrix.m[1][1] = 4096;
    matrix.m[1][2] = 0;
    matrix.m[2][0] = 0;
    matrix.m[2][1] = 0;
    matrix.m[2][2] = 4096;

    RotMatrixY(vars->roll, &matrix);
    RotMatrixZ(instance->rotation.z, &matrix);
    RotMatrixY(instance->rotation.y, &matrix);
    RotMatrixX(instance->rotation.x, &matrix);

    i = 0;
    do
    {
        ApplyMatrixSV(&matrix, &points[i], &points[i]);
        points[i].vx += instance->position.x;
        points[i].vy += instance->position.y;
        points[i].vz += instance->position.z;
        i++;
    } while (i < 3);

    count = 0;
    for (i = 0; i < 4; i++)
    {
        tface[count] = WALLCR_CollideFace(level, (Position *)&points[edges[i][0]], (Position *)&points[edges[i][1]], &hit[count], (Position *)&normals[count]);
        if (tface[count] != NULL)
        {
            count++;
            if (count == 2)
            {
                if (tface[1] != tface[0])
                {
                    break;
                }
                count = 1;
            }
        }
    }

    if (count == 0)
    {
        return 0;
    }

    switch (count)
    {
    case 1:
        flex = 0;
        COPY_SVEC(Normal, &normal, Normal, &normals[0]);
        break;
    case 2:
        pos.x = instance->position.x - hit[0].x;
        pos.y = instance->position.y - hit[0].y;
        pos.z = instance->position.z - hit[0].z;
        dist[0] = (short)((pos.x * normals[0].x) >> 12) + (short)((pos.y * normals[0].y) >> 12) + (short)((pos.z * normals[0].z) >> 12);
        pos.x = instance->position.x - hit[1].x;
        pos.y = instance->position.y - hit[1].y;
        pos.z = instance->position.z - hit[1].z;
        dist[1] = (short)((pos.x * normals[1].x) >> 12) + (short)((pos.y * normals[1].y) >> 12) + (short)((pos.z * normals[1].z) >> 12);

        if (dist[0] < 100)
        {
            dist[0] = 100;
        }
        else if (dist[0] > 200)
        {
            dist[0] = 1;
        }
        else
        {
            dist[0] = 201 - dist[0];
        }

        if (dist[1] < 100)
        {
            dist[1] = 100;
        }
        else if (dist[1] > 200)
        {
            dist[1] = 1;
        }
        else
        {
            dist[1] = 201 - dist[1];
        }

        total = dist[0] + dist[1];
        normal.x = ((dist[0] * normals[0].x) + (dist[1] * normals[1].x)) / total;
        normal.y = ((dist[0] * normals[0].y) + (dist[1] * normals[1].y)) / total;
        normal.z = ((dist[0] * normals[0].z) + (dist[1] * normals[1].z)) / total;
        MATH3D_Normalize(&normal);

        if (dist[0] < dist[1])
        {
            ref = &normals[1];
        }
        else
        {
            ref = &normals[0];
        }
        flex = MATH3D_AngleBetweenVectors((SVector *)&normal, (SVector *)ref);
        break;
    }

    pos.x = instance->position.x - ((normal.x * 75) >> 10);
    pos.y = instance->position.y - ((normal.y * 75) >> 10);
    pos.z = instance->position.z - ((normal.z * 75) >> 10);

    tface[0] = WALLCR_CollideFace(level, &instance->position, &pos, &pos, NULL);
    if (tface[0] != NULL)
    {

        instance->position.x = pos.x + ((normal.x * 75) >> 11);
        instance->position.y = pos.y + ((normal.y * 75) >> 11);
        instance->position.z = pos.z + ((normal.z * 75) >> 11);
        MATH3D_RotationFromPosToPos(&zero, (Position *)&normal, &rotation);

        upsideDown = instance->rotation.y == -1024 && instance->rotation.z == -1024;
        if (!upsideDown ? (rotation.x >= 1024) : (rotation.x < 1024))
        {
            int roll;
            int angle;

            in.vx = 0;
            in.vy = -4096;
            in.vz = 0;
            ApplyMatrixSV(&matrix, &in, &out);
            angle = MATH3D_AngleFromPosToPos(&zero, (Position *)&out) - rotation.z;
            roll = vars->roll + angle;
            extraRot.z = 0;
            extraRot.x = 0;
            extraRot.y = roll;
            G2Anim_SetController_Vector(&instance->anim, 0, G2ANIM_CTRLRTYPE_ADD_LOCALROT, &extraRot);
            vars->roll = roll;
        }

        WALLCR_SetFlex(instance, flex);
        instance->oldTFace = instance->tface;
        instance->tface = tface[0];
        instance->tfaceLevel = level;
        COPY_SVEC(Rotation, &instance->rotation, Rotation, &rotation);
        MATH3D_ZYXtoXYZ(&instance->rotation);
        return 1;
    }

    return 0;
}

int WALLCR_MoveForward(Instance *instance, Position *target)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;
    WallcrVars *vars = (WallcrVars *)mv->extraVars;
    G2SVector3 extraRot;
    short roll;
    int angle;

    angle = WALLCR_AngleFromPosToPos(&instance->position, target, &instance->rotation);
    roll = vars->roll;
    AngleMoveToward(&roll, angle, mv->subAttr->speedRunTurn);
    extraRot.z = 0;
    extraRot.x = 0;
    extraRot.y = roll;
    G2Anim_SetController_Vector(&instance->anim, 0, G2ANIM_CTRLRTYPE_ADD_LOCALROT, &extraRot);
    vars->roll = roll;

    if (!WALLCR_FollowWall(instance))
    {
        MON_PlayAnimFromList(instance, ((MonsterAttributes *)instance->data)->auxAnimList, 6, 2);
        mv->auxFlags &= ~8;
        return 0;
    }

    if (!(mv->auxFlags & 8))
    {
        MON_PlayAnimFromList(instance, ((MonsterAttributes *)instance->data)->auxAnimList, 8, 2);
        mv->auxFlags |= 8;
    }

    return 1;
}

void WALLCR_ApplySpin(Instance *instance, int time)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;
    WallcrVars *vars = (WallcrVars *)mv->extraVars;

    instance->rotation.y += (vars->correct_roll_speed * time) / 4096;
    instance->rotation.x += (vars->pitch_speed * time) / 4096;
    vars->roll += (vars->roll_speed * time) / 4096;

    if (G2Anim_IsControllerActive(&instance->anim, 1, G2ANIM_CTRLRTYPE_ADD_LOCALTRANS))
    {
        G2SVector3 extraRot;

        extraRot.z = 0;
        extraRot.x = 0;
        extraRot.y = vars->roll;
        G2Anim_SetController_Vector(&instance->anim, 0, G2ANIM_CTRLRTYPE_ADD_LOCALROT, &extraRot);
    }

    instance->rotation.z += (vars->yaw_speed * time) / 4096;
}

void WALLCR_AttachEntry(Instance *instance, Position *point, Position *normal)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;
    WallcrVars *vars;
    Position zero = {0};
    Rotation rotation;

    if (mv != NULL)
    {
        vars = (WallcrVars *)mv->extraVars;
        if (vars != NULL)
        {
            MATH3D_RotationFromPosToPos(&zero, normal, &rotation);

            if (instance->position.z < point->z)
            {
                WALLCR_EnableControllers(instance);
                vars->yaw_speed = (((rotation.z - instance->rotation.z) & 0xFFF) > 2048 ? ((rotation.z - instance->rotation.z) & 0xFFF) - 4096 : (rotation.z - instance->rotation.z) & 0xFFF) / 13;
                vars->roll_speed = 0;
                vars->correct_roll_speed = 0;
                vars->roll = 0;
                vars->pitch_speed = (((rotation.x - instance->rotation.x) & 0xFFF) > 2048 ? ((rotation.x - instance->rotation.x) & 0xFFF) - 4096 : (rotation.x - instance->rotation.x) & 0xFFF) / 13;
                vars->time = MON_GetTime(instance);
                instance->xVel = ((point->x + ((normal->x * 75) >> 11)) - instance->position.x) / 13;
                instance->yVel = ((point->y + ((normal->y * 75) >> 11)) - instance->position.y) / 13;
                instance->zVel = 42;
                instance->zAccl = -3;
                mv->mvFlags |= 0x800;
                mv->auxFlags |= 3;
                MON_PlayAnimFromList(instance, ((MonsterAttributes *)instance->data)->auxAnimList, 0, 1);
            }
            else
            {
                printf("oh shit, too high\n");
            }
        }
    }
}

void WALLCR_Attach(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;
    WallcrVars *vars = (WallcrVars *)mv->extraVars;
    int time;
    int last_time;

    if (vars != NULL)
    {
        if (MON_AnimPlayingFromList(instance, ((MonsterAttributes *)instance->data)->auxAnimList, 2))
        {
            if (instance->flags2 & 0x10)
            {
                mv->auxFlags &= ~1;
                mv->auxFlags = (mv->auxFlags & ~8) | 4;
            }
        }
        else
        {
            time = MON_GetTime(instance) - vars->time;
            last_time = time - gameTrackerX.lastLoopTime;

            if (time >= 99)
            {
                WALLCR_ApplySpin(instance, gameTrackerX.timeMult);
            }

            if (time >= 165)
            {
                PhysicsMove(instance, &instance->position, gameTrackerX.timeMult);
            }

            if (last_time < 429 && time >= 429)
            {
                MON_PlayAnimFromList(instance, ((MonsterAttributes *)instance->data)->auxAnimList, 1, 1);
            }

            if (time >= 462)
            {
                if (WALLCR_FollowWall(instance))
                {
                    MON_PlayAnimFromList(instance, ((MonsterAttributes *)instance->data)->auxAnimList, 2, 1);
                    instance->zAccl = 0;
                    instance->zVel = 0;
                    instance->yVel = 0;
                    instance->xVel = 0;
                    vars->roll = 0;
                    vars->pitch_speed = 0;
                    vars->roll_speed = 0;
                    vars->yaw_speed = 0;
                }

                if (time >= 594)
                {
                    WALLCR_DetachEntry(instance, NULL);
                }
            }
        }
    }
}

void WALLCR_DetachEntry(Instance *instance, Position *target)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;
    WallcrVars *vars = (WallcrVars *)mv->extraVars;

    if (vars != NULL)
    {
        if (target == NULL)
        {
            MATRIX matrix;
            SVECTOR in;
            SVECTOR out;

            RotMatrix((SVECTOR *)&instance->rotation, &matrix);
            in.vx = 0;
            in.vy = -20;
            in.vz = 0;
            ApplyMatrixSV(&matrix, &in, &out);
            instance->xVel = out.vx;
            instance->yVel = out.vy;
            instance->zVel = out.vz + 42;
        }
        else
        {
            instance->xVel = (target->x - instance->position.x) / 15;
            instance->yVel = (target->y - instance->position.y) / 15;
            instance->zVel = ((target->z - instance->position.z) + 2236) / 15;
        }

        instance->zAccl = -16;
        vars->yaw_speed = 136;
        vars->pitch_speed = (((-instance->rotation.x) & 0xFFF) > 2048 ? ((-instance->rotation.x) & 0xFFF) - 4096 : (-instance->rotation.x) & 0xFFF) / 15;
        vars->roll_speed = (((-vars->roll) & 0xFFF) > 2048 ? ((-vars->roll) & 0xFFF) - 4096 : (-vars->roll) & 0xFFF) / 15;

        vars->correct_roll_speed = (((-instance->rotation.y) & 0xFFF) > 2048 ? ((-instance->rotation.y) & 0xFFF) - 4096 : (-instance->rotation.y) & 0xFFF) / 15;
        vars->time = MON_GetTime(instance);
        mv->auxFlags = ((mv->auxFlags & ~4) | 1) & ~2;

        if (mv->auxFlags & 0x10)
        {
            MON_PlayAnimFromList(instance, ((MonsterAttributes *)instance->data)->auxAnimList, 11, 1);
        }
        else
        {
            MON_PlayAnimFromList(instance, ((MonsterAttributes *)instance->data)->auxAnimList, 3, 1);
        }
    }
}

void WALLCR_Detach(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;
    WallcrVars *vars = (WallcrVars *)mv->extraVars;

    if (vars != NULL)
    {
        if (MON_AnimPlayingFromList(instance, ((MonsterAttributes *)instance->data)->auxAnimList, 5))
        {
            if (instance->flags2 & 0x10)
            {
                mv->auxFlags &= ~1;
            }
        }
        else
        {
            PhysicsMove(instance, &instance->position, gameTrackerX.timeMult);
            WALLCR_ApplySpin(instance, gameTrackerX.timeMult);

            if ((unsigned long)(MON_GetTime(instance) - vars->time) >= 495)
            {
                WALLCR_DisableControllers(instance);
                vars->roll = 0;
                vars->pitch_speed = 0;
                vars->correct_roll_speed = 0;
                vars->roll_speed = 0;
                vars->yaw_speed = 0;
                instance->rotation.x = 0;
                instance->rotation.y = 0;
            }

            mv->mvFlags &= ~0x800;
            MON_OnGround(instance);

            if (mv->mvFlags & 2)
            {
                WALLCR_DisableControllers(instance);
                instance->rotation.x = 0;
                instance->rotation.y = 0;
                vars->roll = 0;
                instance->zAccl = 0;
                instance->zVel = 0;
                instance->yVel = 0;
                instance->xVel = 0;
                MON_PlayAnimFromList(instance, ((MonsterAttributes *)instance->data)->auxAnimList, 5, 1);
            }
            else
            {
                mv->mvFlags |= 0x800;
            }
        }
    }
}

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

int WALLCR_ShouldSurpriseAttack(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;
    WallcrVars *vars = (WallcrVars *)mv->extraVars;
    MonsterIR *enemy = mv->enemy;
    WallcrAttributes *attributes = (WallcrAttributes *)((MonsterAttributes *)instance->data)->tunData;
    int time;
    int count;
    int chance;

    time = vars->left_over_time + gameTrackerX.timeMult;
    count = time / 4096;
    vars->left_over_time = time - (count * 4096);
    chance = 32767 / (abs(attributes->surprise_attack_time) + 1);

    while (count > 0)
    {
        if (rand() < chance)
        {
            if (enemy != NULL)
            {
                if (PLANAPI_FindClosestNodePositionInUnit(STREAM_GetStreamUnitWithID(instance->currentStreamUnitID),
                                                          &enemy->instance->position, &mv->destination, 0, 32767, 20, 0))
                {
                    return 1;
                }
                break;
            }
            count--;
        }
        else
        {
            count--;
        }
    }

    return 0;
}

int WALLCR_MoveToDestination(Instance *instance, Position *target)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;
    Position pos;
    int result;

    MON_DefaultQueueHandler(instance);

    if (mv->auxFlags & 1)
    {
        if (mv->auxFlags & 2)
        {
            WALLCR_Attach(instance);
        }
        else
        {
            WALLCR_Detach(instance);
        }
    }
    else if (mv->auxFlags & 4)
    {
        result = WALLCR_SeekPath(instance, &pos, &mv->destination, 0, 0x181A1F);
        if (result != 0)
        {
            if (result == 3)
            {
                if (instance->currentMainState == MONSTER_STATE_SURPRISEATTACK)
                {
                    MON_SwitchState(instance, MONSTER_STATE_PURSUE);
                }
            }
            else if (WALLCR_ShouldJumpOn(instance))
            {
                if (ENMYPLAN_GetPosOfNextWaypoint(mv->pathSlotID, &pos))
                {
                    WALLCR_DetachEntry(instance, &pos);
                }
            }
            else
            {
                WALLCR_MoveForward(instance, &pos);
            }
        }
    }
    else
    {
        Position jumpPos;
        Position normal;

        result = WALLCR_SeekPath(instance, &pos, &mv->destination, 0, 0x181A1F);

        if (target != NULL)
        {
            COPY_SVEC(Position, target, Position, &pos);
        }

        if (result != 0)
        {
            if (result != 3)
            {
                if (WALLCR_ShouldJumpOff(instance))
                {
                    ENMYPLAN_GetPosOfNextWaypoint(mv->pathSlotID, &jumpPos);
                    if (MON_TurnToPosition(instance, &jumpPos, mv->subAttr->speedRunTurn) &&
                        WALLCR_FindJumpOnPoint(instance, &jumpPos, &normal) != NULL)
                    {
                        WALLCR_AttachEntry(instance, &jumpPos, &normal);
                        return 1;
                    }
                }
            }
            else if (instance->currentMainState == MONSTER_STATE_SURPRISEATTACK)
            {
                MON_SwitchState(instance, MONSTER_STATE_PURSUE);
                return 1;
            }
        }

        return 0;
    }

    return 1;
}

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

void WALLCR_Init(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;
    WallcrVars *vars;

    MON_DefaultInit(instance);
    G2Anim_AttachControllerToSeg(&instance->anim, 1, G2ANIM_CTRLRTYPE_ADD_LOCALTRANS);
    G2Anim_AttachControllerToSeg(&instance->anim, 0, G2ANIM_CTRLRTYPE_ADD_LOCALROT);
    G2Anim_AttachControllerToSeg(&instance->anim, 18, G2ANIM_CTRLRTYPE_ADD_LOCALROT);
    G2Anim_DisableController(&instance->anim, 1, G2ANIM_CTRLRTYPE_ADD_LOCALTRANS);
    G2Anim_DisableController(&instance->anim, 0, G2ANIM_CTRLRTYPE_ADD_LOCALROT);
    G2Anim_DisableController(&instance->anim, 18, G2ANIM_CTRLRTYPE_ADD_LOCALROT);
    mv->auxFlags &= ~0x20;

    mv = (MonsterVars *)instance->extraData;
    if (mv != NULL)
    {
        vars = (WallcrVars *)MEMPACK_Malloc(sizeof(WallcrVars), 28);
        if (vars == NULL)
        {
            mv->extraVars = NULL;
            return;
        }

        vars->wallcrFlags = 0;
        vars->left_over_time = 0;
        mv->extraVars = vars;

        if (mv->initialBehavior != 16 && !WALLCR_NearFloor(instance) && WALLCR_FollowWall(instance))
        {
            WALLCR_EnableControllers(instance);
            mv->auxFlags |= 4;
            mv->mvFlags |= 0x800;
            MON_SwitchState(instance, MONSTER_STATE_IDLE);
        }
    }
}

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

void WALLCR_SurpriseAttack(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;
    MonsterIR *enemy = mv->enemy;
    Position target;
    Position *jumpTarget;

    if (mv->mvFlags & 4)
    {
        MON_SwitchState(instance, MONSTER_STATE_IDLE);
        MON_DefaultQueueHandler(instance);
    }
    else if (mv->auxFlags & 0x20)
    {
        if (!WALLCR_MoveToDestination(instance, &target))
        {
            if (!MON_AnimPlaying(instance, MONSTER_ANIM_RUN))
            {
                MON_PlayAnim(instance, MONSTER_ANIM_RUN, 2);
            }
            MON_MoveToPosition(instance, &target, mv->subAttr->speedRunTurn);
            mv->mvFlags |= 0x20000;
        }

        if (instance->currentMainState != MONSTER_STATE_FALL)
        {
            if (mv->auxFlags & 0x10)
            {
                if (!(mv->auxFlags & 1))
                {
                    MON_TurnOffWeaponSpheres(instance);
                    mv->auxFlags &= ~0x10;
                    MONSENSE_SetEnemy(instance, gameTrackerX.playerInstance);
                    MON_SwitchState(instance, MONSTER_STATE_PURSUE);
                }
            }
            else if (MATH3D_LengthXYZ(instance->position.x - mv->destination.x,
                                      instance->position.y - mv->destination.y,
                                      instance->position.z - mv->destination.z) < 300)
            {
                jumpTarget = NULL;
                if (enemy != NULL && instance->position.z > mv->destination.z)
                {
                    jumpTarget = &enemy->instance->position;
                }
                mv->auxFlags |= 0x10;
                WALLCR_DetachEntry(instance, jumpTarget);
                MON_TurnOnWeaponSpheres(instance);
            }
        }
    }
    else
    {
        MON_SurpriseAttack(instance);
    }
}

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

void WALLCR_Idle(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;
    MonsterIR *enemy = mv->enemy;

    if (mv->mvFlags & 4)
    {
        if (mv->auxFlags & 4)
        {
            WALLCR_DetachEntry(instance, NULL);
        }

        if (mv->auxFlags & 1)
        {
            MON_DefaultQueueHandler(instance);
            WALLCR_Detach(instance);

            if (mv->auxFlags & 1)
            {
                return;
            }

            MON_IdleEntry(instance);
        }

        MON_Idle(instance);
    }
    else if (mv->auxFlags & 4)
    {
        MON_DefaultQueueHandler(instance);

        if (enemy != NULL)
        {
            MON_SwitchState(instance, MONSTER_STATE_PURSUE);
        }
        else if (WALLCR_ShouldIAmbushPlayer(instance))
        {
            mv->auxFlags |= 0x30;
            WALLCR_DetachEntry(instance, &gameTrackerX.playerInstance->position);
            MON_SwitchState(instance, MONSTER_STATE_SURPRISEATTACK);
        }
        else if (!MON_AnimPlayingFromList(instance, ((MonsterAttributes *)instance->data)->auxAnimList, 7))
        {
            if (rand() < 109)
            {
                MON_PlayAnimFromList(instance, ((MonsterAttributes *)instance->data)->auxAnimList, 7, 1);
            }
        }
        else if (instance->flags2 & 0x10)
        {
            MON_PlayAnimFromList(instance, ((MonsterAttributes *)instance->data)->auxAnimList, 6, 2);
        }
    }
    else
    {
        MON_Idle(instance);
    }
}

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

void WALLCR_ImpactEntry(Instance *instance)
{
    MonsterVars *mv = (MonsterVars *)instance->extraData;
    WallcrVars *vars = (WallcrVars *)mv->extraVars;

    if (vars != NULL)
    {
        if (WALLCR_FollowWall(instance) && instance->tface->textoff != 0xFFFF &&
            (((TextureFT3 *)((char *)((Level *)instance->tfaceLevel)->terrain->StartTextureList + instance->tface->textoff))->attr & 0x800))
        {
            MON_PlayAnimFromList(instance, ((MonsterAttributes *)instance->data)->auxAnimList, 2, 1);
            instance->zAccl = 0;
            instance->zVel = 0;
            instance->yVel = 0;
            instance->xVel = 0;
            vars->roll = 0;
            vars->pitch_speed = 0;
            vars->roll_speed = 0;
            vars->yaw_speed = 0;
            instance->checkMask &= ~0x20;
            mv->mvFlags |= 0x800;
            mv->auxFlags &= ~8;
            mv->mvFlags &= ~2;
        }
        else
        {
            MON_ImpactEntry(instance);
        }
    }
}

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
